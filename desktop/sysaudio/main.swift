import AVFoundation
import AppKit
import AudioToolbox
import CoreAudio
import Darwin
import Foundation
import ScreenCaptureKit

/// Local microphone + system audio helper. Prefer ScreenCaptureKit and fall
/// back to Core Audio taps; GET /pcm streams 16 kHz mono s16le PCM.

let outRate: Double = 16_000

func audioDeviceName(_ id: AudioObjectID) -> String {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioObjectPropertyName,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var cf: CFString?
    var size = UInt32(MemoryLayout<CFString?>.size)
    let err = withUnsafeMutablePointer(to: &cf) { ptr in
        AudioObjectGetPropertyData(id, &addr, 0, nil, &size, ptr)
    }
    guard err == noErr, let name = cf as String? else { return "?" }
    return name
}

func defaultInputDeviceID() -> AudioObjectID {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultInputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dev = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    let err = AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &dev
    )
    return err == noErr ? dev : 0
}

func runOnMain<T>(_ body: @escaping () throws -> T) throws -> T {
    if Thread.isMainThread { return try body() }
    var result: Result<T, Error>!
    let sem = DispatchSemaphore(value: 0)
    DispatchQueue.main.async {
        result = Result { try body() }
        sem.signal()
    }
    sem.wait()
    return try result.get()
}

final class Resampler {
    private var pending: [Float] = []
    private var pos: Double = 0
    var inRate: Double = 48_000
    // 时钟漂移微调（±0.15% 内）。tap 走输出设备的晶振、输出流走麦克风的
    // 晶振，两钟长期单向漂移；与其周期性 hold/drop 制造断裂，不如让变速
    // 吸收——稳态偏差 ≤±50ppm，听感为不可闻的微幅音调偏移。
    var rateAdjust: Double = 1.0

    func reset(rate: Double) {
        inRate = max(1, rate)
        rateAdjust = 1.0
        pending.removeAll(keepingCapacity: true)
        pos = 0
    }

    func pushFloat(_ samples: [Float]) -> [Float] {
        pending.append(contentsOf: samples)
        let step = inRate / outRate * rateAdjust
        var out = [Float]()
        out.reserveCapacity(samples.count / max(1, Int(step)) + 8)
        while pos + 1 < Double(pending.count) {
            let i = Int(pos)
            let frac = Float(pos - Double(i))
            let s = pending[i] * (1 - frac) + pending[i + 1] * frac
            out.append(max(-1, min(1, s)))
            pos += step
        }
        let consumed = Int(pos)
        if consumed > 0 && consumed <= pending.count {
            pending.removeFirst(consumed)
            pos -= Double(consumed)
        }
        return out
    }

    func push(_ samples: [Float]) -> Data {
        let floats = pushFloat(samples)
        if floats.isEmpty { return Data() }
        var out = [Int16]()
        out.reserveCapacity(floats.count)
        for s in floats {
            out.append(Int16((s * 32767).rounded()))
        }
        return out.withUnsafeBytes { Data($0) }
    }
}

/// Default-input capture. `requestAccess` is what shows the macOS TCC dialog;
/// WKWebView getUserMedia never does.
final class MicCapture {
    private let engine = AVAudioEngine()
    private let resampler = Resampler()
    private(set) var running = false
    // 输出流的唯一时钟：麦克风每交付一批重采样后的样本，混音输出就推进
    // 一批（见 Capture.onMicSamples）。曾经由 20ms GCD 定时器自由消费、
    // 队列缺量靠重复上一帧填充——两颗时钟的漂移让填充以每秒几十次的密度
    // 出现，波形被打成采样保持状的平顶阶跃 = 随声音出现的电流声
    // （2026-09-23 对 9-23 真实录音坐实：20 分钟 14.6 万处）。
    var onSamples: (([Float]) -> Void)?
    // 实测交付速率：设备标称速率 ≠ 真实速率（AirPods HFP 实测偏慢
    // 0.19%）。不矫正的话输出流时间轴被拉长，且 tap 队列因「输出慢于
    // 墙钟」持续积压，~8 分钟顶到安全阀开始啃远端音频。每 2s 实测一次
    // （回调级瞬时速率抖动太大），EMA 平滑后折算进 resampler.rateAdjust。
    private var winStartUptime: UInt64 = 0
    private var winFrames = 0
    private var measuredInRate: Double = 0
    // ⚠️ 路由变化自愈（2026-09-24）：反复摘戴耳机会让 AVAudioEngine 的
    // 输入停摆（路由变化后 tap 配置失效，不重启就永远哑掉）——用户实测
    // 「反复摘戴后连自己声音都不录了」。双保险：①监听引擎配置变化事件
    // 自动重启采集；②看门狗——麦克风 6s 没到货（按交付计，非按信号）
    // 强制恢复，兜住通知漏发的场景。
    private var configObserver: NSObjectProtocol?
    private var lastMicDeliveryUptime: UInt64 = 0
    private var lastRecoveryAt = Date.distantPast
    private var micWatchdog: DispatchSourceTimer?
    private var recoveryGeneration = 0

    static func permissionStatus() -> String {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return "authorized"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "notDetermined"
        @unknown default: return "restricted"
        }
    }

    private static let permissionRequest = MicrophonePermissionRequest()

    static func ensureAccess() -> String {
        permissionRequest.resolve(status: permissionStatus()) { done in
            AVCaptureDevice.requestAccess(for: .audio, completionHandler: done)
        }
    }

    func start() throws {
        try runOnMain {
            try self.startOnMain()
        }
    }

    private func startOnMain() throws {
        if running { return }
        let input = engine.inputNode
        let mixer = engine.mainMixerNode
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate > 1, format.channelCount >= 1 else {
            throw NSError(
                domain: "sysaudio",
                code: 20,
                userInfo: [NSLocalizedDescriptionKey: "No microphone input format"]
            )
        }
        // macOS: input taps stay silent unless the node is in the graph.
        engine.connect(input, to: mixer, format: format)
        mixer.outputVolume = 0
        resampler.reset(rate: format.sampleRate)
        winStartUptime = DispatchTime.now().uptimeNanoseconds
        winFrames = 0
        measuredInRate = 0
        let channels = Int(format.channelCount)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.ingest(buffer, channels: channels)
        }
        try engine.start()
        running = true
        installConfigObserver()
        startMicWatchdog()
        FileHandle.standardError.write(
            Data("sysaudio: microphone started device=\"\(audioDeviceName(defaultInputDeviceID()))\" rate=\(Int(format.sampleRate)) ch=\(channels)\n".utf8)
        )
    }

    // ── 路由变化自愈 ──
    private func installConfigObserver() {
        guard configObserver == nil else { return }
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil
        ) { [weak self] _ in
            DispatchQueue.main.async { self?.recoverAfterRouteChange(reason: "config-change") }
        }
    }

    /// 路由变化 / 看门狗触发的恢复：拆 tap → 停引擎 → 按当前输入格式
    /// 重新连接、重装 tap（startOnMain 内会重置重采样与速率测量）。
    /// 输入设备可能暂时无效（耳机刚断开），失败则每秒重试直到恢复。
    private func recoverAfterRouteChange(reason: String) {
        guard running else { return }
        let now = Date()
        guard now.timeIntervalSince(lastRecoveryAt) > 0.5 else { return } // 摘戴抖动防抖
        lastRecoveryAt = now
        recoveryGeneration += 1
        let gen = recoveryGeneration
        do {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            // ⚠️ 必须清位：startOnMain 有 `if running { return }` 守卫，不清
            // 它整个恢复就是空操作——拆了 tap 停了引擎却不重建，麦克风从此
            // 永久哑掉（00:49 实弹 6 次 recovered 零次 microphone started）。
            running = false
            try startOnMain()
            FileHandle.standardError.write(
                Data("sysaudio: mic recovered after route change (reason=\(reason) gen=#\(gen))\n".utf8)
            )
        } catch {
            FileHandle.standardError.write(
                Data("sysaudio: mic recovery failed (reason=\(reason): \(error.localizedDescription)) — retrying\n".utf8)
            )
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                guard let self, self.running, self.recoveryGeneration == gen else { return }
                self.recoverAfterRouteChange(reason: "retry")
            }
        }
    }

    private func startMicWatchdog() {
        micWatchdog?.cancel()
        let t = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
        t.schedule(deadline: .now() + 8, repeating: 3)
        t.setEventHandler { [weak self] in
            guard let self, self.running, self.lastMicDeliveryUptime > 0 else { return }
            let silent = Double(DispatchTime.now().uptimeNanoseconds - self.lastMicDeliveryUptime) / 1e9
            if silent > 6 {
                FileHandle.standardError.write(
                    Data("sysaudio: mic silent \(Int(silent))s — watchdog recovery\n".utf8)
                )
                self.lastMicDeliveryUptime = DispatchTime.now().uptimeNanoseconds // 防止每 3s 重复触发
                DispatchQueue.main.async { self.recoverAfterRouteChange(reason: "watchdog") }
            }
        }
        t.resume()
        micWatchdog = t
    }

    func stop() {
        let work = {
            if self.running {
                self.engine.inputNode.removeTap(onBus: 0)
                self.engine.stop()
                self.running = false
            }
            if let obs = self.configObserver {
                NotificationCenter.default.removeObserver(obs)
                self.configObserver = nil
            }
            self.micWatchdog?.cancel()
            self.micWatchdog = nil
        }
        if Thread.isMainThread {
            work()
        } else {
            DispatchQueue.main.sync(execute: work)
        }
    }

    private func ingest(_ buffer: AVAudioPCMBuffer, channels: Int) {
        guard let data = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        guard frames > 0 else { return }
        var mono = [Float](repeating: 0, count: frames)
        let left = data[0]
        if channels >= 2 {
            let right = data[1]
            for i in 0..<frames { mono[i] = 0.5 * (left[i] + right[i]) }
        } else {
            for i in 0..<frames { mono[i] = left[i] }
        }
        lastMicDeliveryUptime = DispatchTime.now().uptimeNanoseconds
        winFrames += frames
        let nowUptime = DispatchTime.now().uptimeNanoseconds
        let dt = Double(nowUptime - winStartUptime) / 1e9
        if dt >= 2.0, winFrames > 0 {
            let inst = Double(winFrames) / dt
            measuredInRate = measuredInRate == 0 ? inst : measuredInRate + (inst - measuredInRate) * 0.3
            winStartUptime = nowUptime
            winFrames = 0
            if resampler.inRate > 1, measuredInRate > 1 {
                resampler.rateAdjust = max(0.95, min(1.05, measuredInRate / resampler.inRate))
            }
        }
        onSamples?(resampler.pushFloat(mono))
    }
}

// ── ScreenCaptureKit 系统音采集（治本路线，2026-09-23 拍板）──
// CoreAudio process tap 挂在默认输出设备的 aggregate 上，蓝牙 HFP 通话
// 模式下 macOS 按半速交付（播放=高八度+50% 断裂）、渲染管线启停有
// 毛刺（爆音）、摘耳机要重建（咔哒声）。SCK 在渲染层**之前**按 App
// 取音，与输出设备彻底解耦，上述全部绕开。TCC 需要屏幕录制授权；
// preflight 不过或 SCK 起流失败都自动回退旧 tap 路线，不会更糟。
final class SCKTap: NSObject, SCStreamOutput, SCStreamDelegate {
    private let lock = NSRecursiveLock()
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "sinkduce.sysaudio.sck", qos: .userInteractive)
    private var reportedFailure = false
    private var stopped = false
    private let isCurrent: () -> Bool
    var onSamples: (([Float], Double) -> Void)?
    var onFailed: ((String) -> Void)?
    private(set) var running = false

    init(isCurrent: @escaping () -> Bool) {
        self.isCurrent = isCurrent
        super.init()
    }

    func start() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.startOnQueue()
        }
    }

    private func startOnQueue() {
        Task { [weak self] in
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                self?.handleShareableContent(content: content, error: nil)
            } catch {
                self?.handleShareableContent(content: nil, error: error)
            }
        }
    }

    private func handleShareableContent(content: SCShareableContent?, error: (any Error)?) {
        lock.lock()
        defer { lock.unlock() }
        guard !stopped, !reportedFailure, isCurrent() else { return }
        if let error {
            fail("shareable content: \(error.localizedDescription)")
            return
        }
        guard let display = content?.displays.first else {
            fail("no display")
            return
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 2
        config.queueDepth = 12
        // 只采音：视频参数压到最小并忽略 .screen 缓冲
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 2)
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        do {
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        } catch {
            fail("addStreamOutput: \(error.localizedDescription)")
            return
        }
        lock.lock()
        self.stream = stream
        lock.unlock()
        stream.startCapture { [weak self] error in
            guard let self else { return }
            self.lock.lock()
            defer { self.lock.unlock() }
            guard !self.stopped, !self.reportedFailure, self.isCurrent() else { return }
            if let error {
                self.fail("startCapture: \(error.localizedDescription)")
            } else {
                self.running = true
                FileHandle.standardError.write(Data("sysaudio: sck started (48k stereo, pre-device)\n".utf8))
            }
        }
    }

    func stop() {
        lock.lock()
        let s = stream
        stream = nil
        running = false
        stopped = true
        onSamples = nil
        lock.unlock()
        guard let s else { return }
        s.stopCapture { _ in }
    }

    private func fail(_ msg: String) {
        lock.lock()
        let first = !reportedFailure && !stopped && isCurrent()
        reportedFailure = true
        running = false
        stream = nil
        lock.unlock()
        guard first else { return }
        FileHandle.standardError.write(Data("sysaudio: sck failed (\(msg)) — falling back to legacy tap\n".utf8))
        onFailed?(msg)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard isCurrent() else { return }
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(format) else { return }
        let asbd = asbdPtr.pointee
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let nonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0
        let listSize = MemoryLayout<AudioBufferList>.size + max(0, channels - 1) * MemoryLayout<AudioBuffer>.size
        let list = UnsafeMutableRawPointer.allocate(byteCount: listSize, alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { list.deallocate() }
        memset(list, 0, listSize)
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: list.assumingMemoryBound(to: AudioBufferList.self),
            bufferListSize: listSize,
            blockBufferAllocator: kCFAllocatorDefault,
            blockBufferMemoryAllocator: kCFAllocatorDefault,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr,
              let samples = Capture.monoFloats(
                  list.assumingMemoryBound(to: AudioBufferList.self),
                  channels: channels,
                  isFloat: isFloat,
                  nonInterleaved: nonInterleaved
              ), !samples.isEmpty else { return }
        onSamples?(samples, asbd.mSampleRate)
    }

    func stream(_ stream: SCStream, didStopWithError error: any Error) {
        guard running else { return }
        fail("stopped: \(error.localizedDescription)")
    }
}

final class Capture {
    private let lock = NSLock()
    private var clientFD: Int32 = -1
    private let resampler = Resampler()
    private let mic = MicCapture()
    private var sck: SCKTap?
    private let attempts = CaptureAttemptState()
    // SCK 活跃时旧 tap 的 watchdog 不可用（没有 aggregate 可重建）
    private var usingSCK = false
    private let ioQueue = DispatchQueue(label: "sinkduce.sysaudio.io", qos: .userInteractive)
    private let writeQueue = DispatchQueue(label: "sinkduce.sysaudio.write", qos: .userInteractive)
    private let controlQueue = DispatchQueue(label: "sinkduce.sysaudio.control")
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID = AudioDeviceID(0)
    private var ioProcID: AudioDeviceIOProcID?
    private var lastRmsLog = Date.distantPast
    private var outputUID = ""
    private var defaultOutputListener: AudioObjectPropertyListenerBlock?
    private var sawTapAudio = false
    private var tapDeadSince: Date?
    private var rebuiltThisStreak = false
    private var rebuildFailCount = 0
    private var rebuilding = false
    private let tapAliveRms: Float = 0.01
    private let tapDeadRms: Float = 0.005
    private let tapDeadSeconds: TimeInterval = 25
    private let maxRebuildFails = 2
    private(set) var capturing = false

    // ── 混音状态（时钟 = 麦克风，见 onMicSamples）──
    private var tapQueue: [Float] = []
    private var lastTap: Float = 0
    private var tapHoldCount = 0
    private var pendingOut: [Int16] = []
    private var accEnergy: Float = 0
    private var accMicEnergy: Float = 0
    private var accTapEnergy: Float = 0
    private var accSamples: Float = 0
    // tap 饥饿诊断（每 2s 窗口复位）：held=保持样本数，gap=最长连续保持。
    private var accTapHeld: Int = 0
    private var maxTapGap: Int = 0
    private var curTapGap: Int = 0
    // tap 交付诊断：ioProc 回调数 + 原始帧数。frames/wall ≈ 48000=全速，
    // ≈24000=源头半速（HFP 链路被抢/时钟减半），大幅波动=渲染间歇。
    private var accTapIoProcs: Int = 0
    private var accTapIoFrames: Int = 0
    // ⚠️ 蓝牙 HFP 通话模式下 macOS 按半速交付 tap 内容（实测 23:04/23:18
    // 两会话：tapKF≈24k/2s、tapHeld=16000/32000、tapGapMs=50 恒定）——内容
    // 完整但到货时间轴被拉长 2×。混音 1:1 占位会把半速内容在全速槽位里
    // 2 倍速播放 = 高八度 + 50ms 断裂（用户实测听感「高了八度」）。修法
    // 与麦克风侧同源：实测交付速率，重采样按交付比把内容拉伸回真实时长。
    // 量程 ±[0.25,2.0]（HFP 半速=0.5；A2DP/扬声器=1.0），与漂移环相乘。
    private var tapWinStartUptime: UInt64 = 0
    private var tapWinFrames: Int = 0
    private var tapWinProcs: Int = 0
    private var tapDeliveryEMA: Double = 0
    private var tapDeliveryAdjust: Double = 1.0
    // 起步快充/巡航两态（ioQueue 私有，见 handleIO 注释）
    private var tapCruise = false
    // tap 消耗起步门槛：先攒 30ms 再开播，起步段不追队列。
    private static let tapStartGate = 480
    // tap 目标水位 3200 样本 = 200ms 蓝牙抖动缓冲。⚠️ 曾定 320（20ms）：
    // 从水位掉到警戒线只要 14ms，蓝牙 HFP 的真实交付节奏（~120ms 一坨 +
    // 抖动 + 短停顿）必然周期性击穿 → hold/衰减归零反复触发 = 用户听到的
    // 「内录断裂」（2026-09-23 晚 AirPods+Teams 实弹坐实；压测当年用理想
    // 均匀交付节奏，测不出）。压测（stress_bt.swift）钉的参数：200ms
    // 缓冲可整吞 ≤170ms 的交付停顿；±30ms 每批抖动下水位随机游走仍偶触
    // 警戒线（现实抖动相关且更温和）。代价=远端腿比麦克风腿晚 ~200ms，
    // 会议场景不可闻。
    private static let tapTargetLevel = 3200.0
    // 漂移控制环：全权行程 = window×±1500ppm = ±750 样本，温和拉回，
    // 不追抖动（追了就是音调晃），水位漂移由缓冲吸收。
    private static let tapControlWindow = 500_000.0

    private func resetMixState() {
        tapQueue.removeAll(keepingCapacity: true)
        lastTap = 0
        tapHoldCount = 0
        curTapGap = 0
        maxTapGap = 0
        accTapHeld = 0
        accTapIoProcs = 0
        accTapIoFrames = 0
        tapWinStartUptime = 0
        tapWinFrames = 0
        tapWinProcs = 0
        tapDeliveryEMA = 0
        tapDeliveryAdjust = 1.0
        tapCruise = false
        pendingOut.removeAll(keepingCapacity: true)
        accEnergy = 0
        accMicEnergy = 0
        accTapEnergy = 0
        accSamples = 0
    }

    func attach(fd: Int32) {
        lock.lock()
        clientFD = fd
        lock.unlock()
    }

    func detach() {
        lock.lock()
        clientFD = -1
        lock.unlock()
        stop()
    }

    func start() throws {
        var attempt = 0
        try runOnMain { [self] in
            if self.capturing { return }
            attempt = self.attempts.begin()
            self.mic.onSamples = { [weak self] samples in
                guard let self, self.attempts.isCurrent(attempt) else { return }
                self.onMicSamples(samples)
            }
            try self.mic.start()
            self.capturing = true
        }
        guard attempt != 0 else { return }
        // Core Audio process-tap can hang waiting for a TCC sheet this
        // helper cannot show. Never block GET /pcm on it — mic-only first.
        controlQueue.async { [weak self] in
            guard let self, self.attempts.isCurrent(attempt) else { return }
            // 授权请求已在 App 启动时发出（见 SysAudioMain.main）——录制
            // 路径只做静默检查，绝不弹窗打断录制。
            let pf = CGPreflightScreenCaptureAccess()
            FileHandle.standardError.write(Data("sysaudio: sck preflight=\(pf)\n".utf8))
            guard pf else {
                self.startLegacyTap(attempt: attempt)
                return
            }
            let tap = SCKTap(isCurrent: { [weak self] in self?.attempts.isCurrent(attempt) == true })
            tap.onSamples = { [weak self] samples, rate in
                // 经 ioQueue 串行进管线，与旧 tap 同一条路
                self?.ioQueue.async {
                    guard let self, self.attempts.isCurrent(attempt) else { return }
                    self.ingestTap(samples, rate: rate)
                }
            }
            tap.onFailed = { [weak self] _ in
                guard let self, self.attempts.isCurrent(attempt) else { return }
                self.lock.lock()
                self.usingSCK = false
                self.lock.unlock()
                self.controlQueue.async { [weak self] in self?.startLegacyTap(attempt: attempt) }
            }
            self.lock.lock()
            guard self.attempts.isCurrent(attempt) else {
                self.lock.unlock()
                return
            }
            self.usingSCK = true
            self.sck = tap
            self.lock.unlock()
            tap.start()
        }
    }

    private func startLegacyTap(attempt: Int) {
        guard attempts.isCurrent(attempt) else { return }
        do {
            try startTap()
            if !attempts.isCurrent(attempt) {
                removeDefaultOutputListener()
                destroyAggregate()
                destroyTap()
            }
        } catch {
            FileHandle.standardError.write(
                Data("sysaudio: tap skipped (\(error.localizedDescription)) — mic only\n".utf8)
            )
        }
    }

    func warmup() {
        // Status poll only. Requesting access here used to pop a mic TCC
        // sheet on every meeting-page load; the formal ask lives on /pcm —
        // the explicit Start Recording click.
        let status = AVCaptureDevice.authorizationStatus(for: .audio)
        let state =
            status == .authorized ? "granted"
            : (status == .denied || status == .restricted) ? "denied"
            : "undetermined"
        FileHandle.standardError.write(
            Data("sysaudio: warmup done tcc=\(state)\n".utf8)
        )
    }

    private static let chunkSize = 320 // 20ms at 16 kHz

    /// 麦克风时钟驱动的混音输出：麦克风每交付 N 个样本，输出流恰好推进 N 个
    /// 样本，永不合成、永不重复。tap 与麦克风的时钟漂移由 handleIO 里的
    /// 变速控制环吸收；tap 短暂缺量才 hold 上一帧（超 80ms 衰减为零）。
    private func onMicSamples(_ mic: [Float]) {
        guard !mic.isEmpty else { return }
        var out = [Int16]()
        out.reserveCapacity(mic.count)
        var energy: Float = 0
        var micEnergy: Float = 0
        var tapEnergy: Float = 0
        var logLine: String?
        var tapRms: Float = 0
        lock.lock()
        let fd = clientFD
        // 读游标 + 一次性压缩：RT 线程上逐样本 removeFirst 是 O(n²) 搬移。
        var read = 0
        let avail = tapQueue.count
        for m in mic {
            let t: Float
            if avail - read >= Self.tapStartGate {
                t = tapQueue[read]
                read += 1
                lastTap = t
                tapHoldCount = 0
                curTapGap = 0
            } else {
                tapHoldCount += 1
                curTapGap += 1
                if curTapGap > maxTapGap { maxTapGap = curTapGap }
                accTapHeld += 1
                t = tapHoldCount > Self.chunkSize * 4 ? 0 : lastTap
            }
            micEnergy += m * m
            tapEnergy += t * t
            let s = max(-1, min(1, m + t))
            energy += s * s
            out.append(Int16((s * 32767).rounded()))
        }
        if read > 0 { tapQueue.removeFirst(read) }
        if fd >= 0 {
            pendingOut.append(contentsOf: out)
            accEnergy += energy
            accMicEnergy += micEnergy
            accTapEnergy += tapEnergy
            accSamples += Float(out.count)
            let now = Date()
            if now.timeIntervalSince(lastRmsLog) >= 2, accSamples > 0 {
                lastRmsLog = now
                let rms = (accEnergy / accSamples).squareRoot()
                let micRms = (accMicEnergy / accSamples).squareRoot()
                tapRms = (accTapEnergy / accSamples).squareRoot()
                let gapMs = Double(maxTapGap) / 16.0
                let ts = ISO8601DateFormatter().string(from: now)
                let qLevel = tapQueue.count
                logLine = "sysaudio: t=\(ts) rms=\(String(format: "%.4f", rms)) mic=\(String(format: "%.4f", micRms)) tap=\(String(format: "%.4f", tapRms)) tapHeld=\(accTapHeld) tapGapMs=\(String(format: "%.0f", gapMs)) tapIo=\(accTapIoProcs) tapKF=\(accTapIoFrames / 1000) tapQ=\(qLevel) tapAdj=\(String(format: "%.2f", tapDeliveryAdjust))\n"
                accEnergy = 0
                accMicEnergy = 0
                accTapEnergy = 0
                accSamples = 0
                accTapHeld = 0
                maxTapGap = 0
                accTapIoProcs = 0
                accTapIoFrames = 0
            }
        }
        var payload: [Int16] = []
        if fd >= 0, pendingOut.count >= Self.chunkSize {
            let flushable = pendingOut.count / Self.chunkSize * Self.chunkSize
            payload = Array(pendingOut.prefix(flushable))
            pendingOut.removeFirst(flushable)
        }
        lock.unlock()
        if fd >= 0, !payload.isEmpty {
            writeQueue.async { [weak self] in
                guard let self else { return }
                // 字节序列化留在写线程，别占音频 RT 线程的预算。
                var data = Data()
                data.reserveCapacity(payload.count * 2)
                for v in payload {
                    withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) }
                }
                if !Self.writeChunk(fd: fd, body: data) {
                    self.detach()
                }
            }
        }
        if let line = logLine {
            FileHandle.standardError.write(Data(line.utf8))
            let captured = tapRms
            controlQueue.async { [weak self] in
                self?.noteTapEnergy(captured)
            }
        }
    }

    private func startTap() throws {
        let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        tapDescription.uuid = UUID()
        tapDescription.muteBehavior = .unmuted
        tapDescription.isPrivate = true
        tapDescription.name = "SinkDuce System Audio"
        let tapUID = tapDescription.uuid.uuidString

        var newTap = AudioObjectID(kAudioObjectUnknown)
        var err = AudioHardwareCreateProcessTap(tapDescription, &newTap)
        guard err == noErr, newTap != kAudioObjectUnknown else {
            throw nsErr(4, "System audio tap failed (\(err)). Allow SinkDuce to capture audio if macOS asks, then Cmd+Q and reopen.")
        }
        tapID = newTap

        let outputUID = try defaultOutputUID()
        self.outputUID = outputUID
        let aggDesc: [String: Any] = [
            kAudioAggregateDeviceNameKey: "SinkDuce System Audio",
            kAudioAggregateDeviceUIDKey: "app.sinkduce.desktop.tap.\(UUID().uuidString)",
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [
                [kAudioSubDeviceUIDKey: outputUID],
            ],
            kAudioAggregateDeviceTapListKey: [
                [
                    kAudioSubTapUIDKey: tapUID,
                    kAudioSubTapDriftCompensationKey: true,
                ],
            ],
        ]
        var agg = AudioDeviceID(0)
        err = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &agg)
        guard err == noErr, agg != 0 else {
            destroyTap()
            throw nsErr(5, "Aggregate device failed (\(err))")
        }
        aggregateID = agg

        let asbd = try tapFormat(newTap)
        if asbd.mSampleRate > 1 {
            resampler.reset(rate: asbd.mSampleRate)
        }
        let channels = max(1, Int(asbd.mChannelsPerFrame))
        let isFloat = (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0
        let isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0

        var proc: AudioDeviceIOProcID?
        err = AudioDeviceCreateIOProcIDWithBlock(&proc, aggregateID, ioQueue) { [weak self] _, inInputData, _, _, _ in
            self?.handleIO(
                inInputData,
                channels: channels,
                isFloat: isFloat,
                nonInterleaved: isNonInterleaved,
                rate: asbd.mSampleRate
            )
        }
        guard err == noErr, let proc else {
            destroyAggregate()
            destroyTap()
            throw nsErr(6, "Audio IO proc failed (\(err))")
        }
        ioProcID = proc
        err = AudioDeviceStart(aggregateID, proc)
        guard err == noErr else {
            destroyAggregate()
            destroyTap()
            throw nsErr(7, "AudioDeviceStart failed (\(err))")
        }
        installDefaultOutputListener()
        FileHandle.standardError.write(
            Data("sysaudio: capture started (coreaudio tap) device=\"\(audioDeviceName(try defaultOutputDeviceID()))\" rate=\(Int(asbd.mSampleRate)) ch=\(channels) float=\(isFloat ? "yes" : "no") interleaved=\(isNonInterleaved ? "no" : "yes")\n".utf8)
        )
    }

    private func noteTapEnergy(_ tapRms: Float) {
        lock.lock()
        let sck = usingSCK
        lock.unlock()
        if sck { return } // SCK 无 aggregate 可重建，静默即静默
        if tapRms >= tapAliveRms {
            sawTapAudio = true
            tapDeadSince = nil
            rebuiltThisStreak = false
            return
        }
        guard sawTapAudio, rebuildFailCount < maxRebuildFails, !rebuiltThisStreak, !rebuilding else {
            return
        }
        if tapRms >= tapDeadRms {
            tapDeadSince = nil
            return
        }
        let now = Date()
        if tapDeadSince == nil {
            tapDeadSince = now
            return
        }
        guard now.timeIntervalSince(tapDeadSince!) >= tapDeadSeconds else { return }
        rebuiltThisStreak = true
        rebuildTap(reason: "watchdog")
    }

    private func rebuildTap(reason: String) {
        guard capturing, !rebuilding else { return }
        if reason == "watchdog", rebuildFailCount >= maxRebuildFails { return }
        rebuilding = true
        FileHandle.standardError.write(
            Data("sysaudio: tap rebuild reason=\(reason)\n".utf8)
        )
        destroyAggregate()
        destroyTap()
        lock.lock()
        resetMixState()
        lock.unlock()
        do {
            try startTap()
            rebuildFailCount = 0
            rebuiltThisStreak = true
            tapDeadSince = nil
            FileHandle.standardError.write(Data("sysaudio: tap rebuild ok\n".utf8))
        } catch {
            rebuiltThisStreak = false
            tapDeadSince = Date()
            rebuildFailCount += 1
            if rebuildFailCount >= maxRebuildFails {
                FileHandle.standardError.write(
                    Data("sysaudio: tap rebuild failed, giving up (\(error.localizedDescription))\n".utf8)
                )
            } else {
                FileHandle.standardError.write(
                    Data("sysaudio: tap rebuild failed (\(error.localizedDescription))\n".utf8)
                )
            }
        }
        rebuilding = false
    }

    private func installDefaultOutputListener() {
        guard defaultOutputListener == nil else { return }
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
            guard let self else { return }
            self.controlQueue.async { self.onDefaultOutputChanged() }
        }
        let err = AudioObjectAddPropertyListenerBlock(
            AudioObjectID(kAudioObjectSystemObject),
            &addr,
            controlQueue,
            block
        )
        if err == noErr {
            defaultOutputListener = block
        }
    }

    private func removeDefaultOutputListener() {
        guard let block = defaultOutputListener else { return }
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        _ = AudioObjectRemovePropertyListenerBlock(
            AudioObjectID(kAudioObjectSystemObject),
            &addr,
            controlQueue,
            block
        )
        defaultOutputListener = nil
    }

    private func onDefaultOutputChanged() {
        guard capturing else { return }
        let next = (try? defaultOutputUID()) ?? ""
        FileHandle.standardError.write(
            Data("sysaudio: default output uid now=\(next) was=\(outputUID)\n".utf8)
        )
        guard !next.isEmpty, next != outputUID else { return }
        rebuildTap(reason: "output-uid")
    }

    private func handleIO(
        _ input: UnsafePointer<AudioBufferList>,
        channels: Int,
        isFloat: Bool,
        nonInterleaved: Bool,
        rate: Double
    ) {
        // ⚠️ 只读 input。对 tap 聚合设备，output 缓冲镜像的是系统正在渲染的
        // 音频——把它也加进信号等于同一份声音错开几十毫秒相加 = 梳状滤波，
        // 听感为金属感/电流感失真，且只在有声音时出现（2026-09-21 定位）。
        guard let samples = Self.monoFloats(
            input,
            channels: channels,
            isFloat: isFloat,
            nonInterleaved: nonInterleaved
        ), !samples.isEmpty else { return }
        ingestTap(samples, rate: rate)
    }

    /// tap 内容进混音管线。旧 tap 的 IOProc 与 SCK 回调都经 ioQueue 串行
    /// 进入（SCK 在 onSamples 里转投），tapWin*/resampler 始终单线程触碰。
    private func ingestTap(_ samples: [Float], rate: Double) {
        if rate > 1 && abs(rate - resampler.inRate) > 1 {
            resampler.reset(rate: rate)
        }
        // ── 实测交付速率（ioQueue 串行，无需加锁）──
        let nowUptime = DispatchTime.now().uptimeNanoseconds
        tapWinFrames += samples.count
        tapWinProcs += 1
        if tapWinStartUptime == 0 {
            tapWinStartUptime = nowUptime
        } else {
            let dt = Double(nowUptime - tapWinStartUptime) / 1e9
            // 统一 2s 窗。曾给首窗 0.5s「快速锁定」——结果吞下启动预滚积压
            // 测出 2× 假速率，适配系数顶到上限 = 录制开头倍速播放（2026-09-24
            // 用户实测）。慢锁定 + 下面的移动限速器才是稳的。
            if dt >= 2.0, tapWinFrames > 0 {
                // 窗口健康检查：正常满速 ≈98 procs/s、HFP 半速 ≈52/s；设备切换/
                // 停顿的瞬态窗只有个位数——瞬态窗的「速率」是垃圾，整窗丢弃、
                // 保留旧 adjust。否则 00:59 实测：切换瞬态被当成 0.25× 交付，
                // tapAdj 贴钳位十余秒，内容全程低八度拉长（用户听感「变低拉长」）。
                let procsPerSec = Double(tapWinProcs) / dt
                if procsPerSec >= 40 {
                    let inst = Double(tapWinFrames) / dt
                    tapDeliveryEMA = tapDeliveryEMA == 0 ? inst : tapDeliveryEMA + (inst - tapDeliveryEMA) * 0.3
                    tapWinStartUptime = nowUptime
                    tapWinFrames = 0
                    tapWinProcs = 0
                    if resampler.inRate > 1, tapDeliveryEMA > 1 {
                        let target = max(0.25, min(2.0, tapDeliveryEMA / resampler.inRate))
                        // 移动限速：每窗最多 ±0.25。单窗垃圾读数（启动预滚、
                        // 瞬态）最多造成 12% 偏差且两窗收回，杜绝倍速级走飞。
                        tapDeliveryAdjust = max(tapDeliveryAdjust - 0.25, min(tapDeliveryAdjust + 0.25, target))
                    }
                } else {
                    // 不健康窗：只重开窗，不动任何状态
                    tapWinStartUptime = nowUptime
                    tapWinFrames = 0
                    tapWinProcs = 0
                }
            }
        }
        let tap = resampler.pushFloat(samples)
        guard !tap.isEmpty else { return }
        lock.lock()
        accTapIoProcs += 1
        accTapIoFrames += samples.count
        // 时钟漂移控制环：巡航期温和拉回（±1500ppm，不追抖动）；水位未达
        // 目标前走「起步快充」（±2%）——否则 2700 样本缺口以 24/s 的速度要
        // 充 2 分钟，期间水位贴着闸门，蓝牙一抖就打出 6-13ms 微断裂（用户
        // 听感的偶发爆音，2026-09-23 深夜坐实：tapHeld 0-450、tapQ 479-755）。
        // 快充 2% = 34 音分，充到即停，不可闻。
        let level = Double(tapQueue.count + tap.count)
        let driftAdjust: Double
        if !tapCruise {
            driftAdjust = level >= Self.tapTargetLevel ? 1.0 : 0.98
            if level >= Self.tapTargetLevel { tapCruise = true }
        } else {
            if level < Self.tapTargetLevel / 2 { tapCruise = false }
            driftAdjust = max(
                0.9985, min(1.0015, 1.0 + (level - Self.tapTargetLevel) / Self.tapControlWindow)
            )
        }
        resampler.rateAdjust = max(0.25, min(2.0, driftAdjust * tapDeliveryAdjust))
        tapQueue.append(contentsOf: tap)
        // 安全阀（控制环正常时永远不会触发）：防 tap 停顿后突发灌入。
        if tapQueue.count > 16_000 {
            tapQueue.removeFirst(tapQueue.count - 16_000)
        }
        lock.unlock()
    }

    func stop() {
        attempts.cancel()
        capturing = false
        sawTapAudio = false
        tapDeadSince = nil
        rebuiltThisStreak = false
        rebuildFailCount = 0
        rebuilding = false
        outputUID = ""
        lock.lock()
        let oldSck = sck
        sck = nil
        usingSCK = false
        lock.unlock()
        oldSck?.stop()
        mic.stop()
        mic.onSamples = nil
        lock.lock()
        resetMixState()
        lock.unlock()
        // Serialized with legacy setup: late setup cannot destroy the next
        // attempt's handles, and stopping the mic never waits on a TCC sheet.
        controlQueue.async { [weak self] in
            self?.removeDefaultOutputListener()
            self?.destroyAggregate()
            self?.destroyTap()
        }
        FileHandle.standardError.write(Data("sysaudio: capture stopped\n".utf8))
    }

    private func destroyAggregate() {
        if let proc = ioProcID, aggregateID != 0 {
            AudioDeviceStop(aggregateID, proc)
            AudioDeviceDestroyIOProcID(aggregateID, proc)
        }
        ioProcID = nil
        if aggregateID != 0 {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = 0
        }
    }

    private func destroyTap() {
        if tapID != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }

    private func nsErr(_ code: Int, _ msg: String) -> NSError {
        NSError(domain: "sysaudio", code: code, userInfo: [NSLocalizedDescriptionKey: msg])
    }

    private func defaultOutputDeviceID() throws -> AudioDeviceID {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var dev = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        let err = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &dev
        )
        guard err == noErr, dev != 0 else {
            throw nsErr(8, "No default output device (\(err))")
        }
        return dev
    }

    private func defaultOutputUID() throws -> String {
        let dev = try defaultOutputDeviceID()
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var cf: CFString?
        var size = UInt32(MemoryLayout<CFString?>.size)
        let err = withUnsafeMutablePointer(to: &cf) { ptr in
            AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, ptr)
        }
        guard err == noErr, let uid = cf as String? else {
            throw nsErr(9, "Output device UID failed (\(err))")
        }
        return uid
    }

    private func tapFormat(_ id: AudioObjectID) throws -> AudioStreamBasicDescription {
        var addr = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let err = AudioObjectGetPropertyData(id, &addr, 0, nil, &size, &asbd)
        guard err == noErr else {
            asbd.mSampleRate = 48_000
            asbd.mFormatID = kAudioFormatLinearPCM
            asbd.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked
            asbd.mChannelsPerFrame = 2
            asbd.mBitsPerChannel = 32
            asbd.mBytesPerFrame = 8
            asbd.mFramesPerPacket = 1
            asbd.mBytesPerPacket = 8
            return asbd
        }
        return asbd
    }

    static func monoFloats(        _ list: UnsafePointer<AudioBufferList>,
        channels: Int,
        isFloat: Bool,
        nonInterleaved: Bool
    ) -> [Float]? {
        let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: list))
        guard let first = abl.first, let p0 = first.mData else { return nil }
        if isFloat && nonInterleaved {
            let frames = Int(first.mDataByteSize) / MemoryLayout<Float>.size
            guard frames > 0 else { return nil }
            let left = p0.assumingMemoryBound(to: Float.self)
            var mono = [Float](repeating: 0, count: frames)
            if abl.count >= 2, let p1 = abl[1].mData {
                let right = p1.assumingMemoryBound(to: Float.self)
                for i in 0..<frames { mono[i] = 0.5 * (left[i] + right[i]) }
            } else {
                for i in 0..<frames { mono[i] = left[i] }
            }
            return mono
        }
        if isFloat {
            let ch = max(1, channels)
            let frames = Int(first.mDataByteSize) / (MemoryLayout<Float>.size * ch)
            guard frames > 0 else { return nil }
            let src = p0.assumingMemoryBound(to: Float.self)
            var mono = [Float](repeating: 0, count: frames)
            for i in 0..<frames {
                if ch >= 2 {
                    mono[i] = 0.5 * (src[i * ch] + src[i * ch + 1])
                } else {
                    mono[i] = src[i]
                }
            }
            return mono
        }
        let ch = max(1, channels)
        let frames = Int(first.mDataByteSize) / (MemoryLayout<Int16>.size * ch)
        guard frames > 0 else { return nil }
        let src = p0.assumingMemoryBound(to: Int16.self)
        var mono = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            if ch >= 2 {
                mono[i] = 0.5 * (Float(src[i * ch]) + Float(src[i * ch + 1])) / 32768
            } else {
                mono[i] = Float(src[i]) / 32768
            }
        }
        return mono
    }

    static func writeChunk(fd: Int32, body: Data) -> Bool {
        let header = String(format: "%lx\r\n", body.count)
        if !writeAll(fd: fd, Data(header.utf8)) { return false }
        if !writeAll(fd: fd, body) { return false }
        return writeAll(fd: fd, Data("\r\n".utf8))
    }

    static func writeAll(fd: Int32, _ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            var sent = 0
            let total = raw.count
            let base = raw.bindMemory(to: UInt8.self).baseAddress!
            while sent < total {
                let n = Darwin.write(fd, base + sent, total - sent)
                if n <= 0 { return false }
                sent += n
            }
            return true
        }
    }
}

let capture = Capture()
let captureRequestLock = NSLock()

func writeHTTP(fd: Int32, status: String, contentType: String, body: Data, extra: [String] = []) {
    var head = "HTTP/1.1 \(status)\r\n"
    head += "Content-Type: \(contentType)\r\n"
    head += "Content-Length: \(body.count)\r\n"
    head += "Access-Control-Allow-Origin: *\r\n"
    head += "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
    head += "Access-Control-Allow-Headers: *\r\n"
    head += "Connection: close\r\n"
    for line in extra { head += line + "\r\n" }
    head += "\r\n"
    _ = Capture.writeAll(fd: fd, Data(head.utf8))
    _ = Capture.writeAll(fd: fd, body)
}

func handleClient(fd: Int32) {
    defer { Darwin.close(fd) }
    var buf = [UInt8](repeating: 0, count: 8192)
    var collected = Data()
    let deadline = Date().addingTimeInterval(5)
    while collected.range(of: Data("\r\n\r\n".utf8)) == nil {
        if Date() > deadline { return }
        let n = Darwin.read(fd, &buf, buf.count)
        if n <= 0 { return }
        collected.append(contentsOf: buf[0..<n])
        if collected.count > 32_768 { return }
    }
    guard let text = String(data: collected, encoding: .utf8) else { return }
    let requestLine = text.split(separator: "\r\n", maxSplits: 1, omittingEmptySubsequences: false)
        .first.map(String.init) ?? ""
    let parts = requestLine.split(separator: " ")
    let method = parts.count > 0 ? String(parts[0]) : ""
    let path = parts.count > 1 ? String(parts[1].split(separator: "?").first ?? parts[1]) : "/"

    if method == "OPTIONS" {
        writeHTTP(fd: fd, status: "204 No Content", contentType: "text/plain", body: Data())
        return
    }
    if path == "/health" {
        let json = "{\"ok\":true,\"capturing\":\(capture.capturing)}\n"
        writeHTTP(fd: fd, status: "200 OK", contentType: "application/json", body: Data(json.utf8))
        return
    }
    if path == "/permissions" {
        let json = "{\"microphone\":\"\(MicCapture.permissionStatus())\",\"screen_recording\":\(CGPreflightScreenCaptureAccess()),\"capturing\":\(capture.capturing)}\n"
        writeHTTP(fd: fd, status: "200 OK", contentType: "application/json", body: Data(json.utf8))
        return
    }
    if path == "/stop" {
        capture.detach()
        writeHTTP(fd: fd, status: "200 OK", contentType: "application/json", body: Data("{\"ok\":true}\n".utf8))
        return
    }
    if path == "/warmup" {
        capture.warmup()
        writeHTTP(fd: fd, status: "200 OK", contentType: "application/json", body: Data("{\"ok\":true}\n".utf8))
        return
    }
    if path == "/pcm" && (method == "GET" || method == "POST") {
        let access = MicCapture.ensureAccess()
        if access == "pending" {
            let json = "{\"error\":\"permission_pending\",\"message\":\"Waiting for the macOS microphone permission dialog.\"}\n"
            writeHTTP(fd: fd, status: "409 Conflict", contentType: "application/json", body: Data(json.utf8))
            return
        }
        if access != "authorized" {
            let msg = "Allow SinkDuce in System Settings → Privacy & Security → Microphone, then quit with Cmd+Q and reopen."
            FileHandle.standardError.write(Data("sysaudio: microphone_permission denied\n".utf8))
            let json = "{\"error\":\"microphone_permission\",\"message\":\"\(msg)\"}\n"
            writeHTTP(fd: fd, status: "403 Forbidden", contentType: "application/json", body: Data(json.utf8))
            return
        }
        guard captureRequestLock.try() else {
            writeHTTP(fd: fd, status: "409 Conflict", contentType: "application/json", body: Data("{\"error\":\"capture_busy\",\"message\":\"Another recording is already using audio capture.\"}".utf8))
            return
        }
        defer { captureRequestLock.unlock() }
        do {
            try capture.start()
        } catch {
            capture.detach()
            FileHandle.standardError.write(Data("sysaudio: start failed \(error.localizedDescription)\n".utf8))
            writeHTTP(fd: fd, status: "500 Internal Server Error", contentType: "application/json", body: Data("{\"error\":\"capture_start_failed\",\"message\":\"Could not start audio capture. Check the microphone and try again.\"}".utf8))
            return
        }
        // Headers first so WKWebView/curl never see an empty reply if tap setup hangs.
        var head = "HTTP/1.1 200 OK\r\n"
        head += "Content-Type: application/octet-stream\r\n"
        head += "Transfer-Encoding: chunked\r\n"
        head += "Access-Control-Allow-Origin: *\r\n"
        head += "Cache-Control: no-store\r\n"
        head += "Connection: keep-alive\r\n\r\n"
        if !Capture.writeAll(fd: fd, Data(head.utf8)) {
            capture.detach()
            return
        }
        capture.attach(fd: fd)
        // Block this thread until the client disconnects (writeChunk fails → detach).
        while capture.capturing {
            Thread.sleep(forTimeInterval: 0.25)
            var probe: Int32 = 0
            var len = socklen_t(MemoryLayout<Int32>.size)
            if getsockopt(fd, SOL_SOCKET, SO_ERROR, &probe, &len) != 0 || probe != 0 {
                break
            }
        }
        capture.detach()
        _ = Capture.writeAll(fd: fd, Data("0\r\n\r\n".utf8))
        return
    }
    writeHTTP(fd: fd, status: "404 Not Found", contentType: "application/json", body: Data("{\"error\":\"not_found\"}\n".utf8))
}

func parsePort() -> UInt16 {
    let args = CommandLine.arguments
    if let i = args.firstIndex(of: "--port"), i + 1 < args.count, let p = UInt16(args[i + 1]), p > 0 {
        return p
    }
    return 18950
}

@main
struct SysAudioMain {
    static func main() {
        let port = parsePort()
        let fd = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else {
            fputs("sysaudio: socket failed\n", stderr)
            exit(1)
        }
        var yes: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, socklen_t(MemoryLayout<Int32>.size))
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = in_port_t(port.bigEndian)
        addr.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let bindRC = withUnsafePointer(to: &addr) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
            }
        }
        guard bindRC == 0, Darwin.listen(fd, 8) == 0 else {
            fputs("sysaudio: bind/listen \(port) failed\n", stderr)
            exit(1)
        }
        fputs("sysaudio: listening 127.0.0.1:\(port)\n", stderr)

        // 用户已拍板启用 SCK 新路线（2026-09-24）：App 启动即请求屏幕录制
        // 授权（若未授予），不要等到第一次录音时才弹——那时弹窗会打断录制
        // 流程。macOS 对同一进程只弹一次；用户在系统设置打开开关并重启
        // App 后，SCK 自动生效（录制路径只做静默检查）。
        let acceptQueue = DispatchQueue(label: "sinkduce.sysaudio.accept", attributes: .concurrent)
        acceptQueue.async {
            while true {
                let cfd = Darwin.accept(fd, nil, nil)
                if cfd < 0 { continue }
                acceptQueue.async {
                    handleClient(fd: cfd)
                }
            }
        }
        // Health must remain available while the user considers the sheet.
        DispatchQueue.main.async {
            if !CGPreflightScreenCaptureAccess() {
                _ = CGRequestScreenCaptureAccess()
            }
        }
        // A detached helper must not outlive its spawner: an orphan keeps its
        // port squatted (pushing the next launch onto ephemeral ports) and
        // re-fires TCC mic prompts with no app around. Covers parents killed
        // with SIGKILL, where no atexit cleanup can run.
        let originPPID = getppid()
        let parentWatch = DispatchSource.makeTimerSource(
            queue: DispatchQueue.global(qos: .utility)
        )
        parentWatch.schedule(deadline: .now() + 5, repeating: 5)
        parentWatch.setEventHandler {
            if getppid() != originPPID {
                fputs("sysaudio: spawner gone — exiting\n", stderr)
                exit(0)
            }
        }
        parentWatch.resume()
        RunLoop.main.run()
    }
}
