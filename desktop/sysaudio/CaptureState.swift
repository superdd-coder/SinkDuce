import Foundation

/// A pending OS sheet belongs to one request, regardless of HTTP retries.
final class MicrophonePermissionRequest {
    private let lock = NSLock()
    private var pending = false

    func resolve(status: String, request: (@escaping (Bool) -> Void) -> Void) -> String {
        if status != "notDetermined" { return status }
        lock.lock()
        if pending {
            lock.unlock()
            return "pending"
        }
        pending = true
        lock.unlock()
        request { [weak self] _ in
            guard let self else { return }
            self.lock.lock()
            self.pending = false
            self.lock.unlock()
        }
        return "pending"
    }
}

/// Invalidates callbacks from stopped or replaced capture attempts.
final class CaptureAttemptState {
    private let lock = NSLock()
    private var generation = 0
    private var active = false

    func begin() -> Int {
        lock.lock()
        defer { lock.unlock() }
        generation += 1
        active = true
        return generation
    }

    func cancel() {
        lock.lock()
        active = false
        generation += 1
        lock.unlock()
    }

    func isCurrent(_ token: Int) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return active && generation == token
    }
}
