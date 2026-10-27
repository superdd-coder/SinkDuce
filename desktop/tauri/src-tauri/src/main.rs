#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_updater::UpdaterExt;

mod audio_helper;
mod office_runtime;
use audio_helper::AudioHelper;

struct TrayState(Mutex<Option<TrayIcon>>);

fn enterprise_build() -> bool {
    env!("SINKDUCE_DESKTOP_EDITION") == "enterprise"
}

fn app_name() -> &'static str {
    if enterprise_build() {
        "SinkDuce-EE"
    } else {
        "SinkDuce"
    }
}

fn data_dir_name(enterprise: bool) -> &'static str {
    if enterprise {
        "SinkDuce-EE"
    } else {
        "SinkDuce"
    }
}

fn preferred_ports(enterprise: bool) -> &'static [u16] {
    if enterprise {
        &[18920, 18921, 18922, 18923, 18924]
    } else {
        &[18910, 18911, 18912, 18913, 18914]
    }
}

/// Monochrome menubar template icon (lake silhouette, black + alpha).
/// icon_as_template(true) lets macOS recolour it for light/dark menubars.
const TRAY_TEMPLATE_PNG: &[u8] = include_bytes!("../icons/tray-template.png");

struct Sidecars {
    children: Mutex<Vec<Child>>,
}

impl Sidecars {
    fn new() -> Self {
        Self {
            children: Mutex::new(Vec::new()),
        }
    }

    fn push(&self, child: Child) {
        self.children.lock().expect("sidecars lock").push(child);
    }

    fn kill_all(&self) {
        if let Ok(mut kids) = self.children.lock() {
            for child in kids.iter_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
            kids.clear();
        }
    }
}

impl Drop for Sidecars {
    fn drop(&mut self) {
        self.kill_all();
    }
}

fn port_free(port: u16) -> bool {
    TcpListener::bind(("127.0.0.1", port)).is_ok()
}

fn something_listening(port: u16) -> bool {
    let v4 = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    if TcpStream::connect_timeout(&v4, Duration::from_millis(150)).is_ok() {
        return true;
    }
    if let Ok(v6) = format!("[::1]:{port}").parse() {
        if TcpStream::connect_timeout(&v6, Duration::from_millis(150)).is_ok() {
            return true;
        }
    }
    false
}

fn pick_port(candidates: &[u16]) -> Result<u16, String> {
    for port in candidates {
        if !something_listening(*port) && port_free(*port) {
            return Ok(*port);
        }
    }
    TcpListener::bind("127.0.0.1:0")
        .map_err(|e| format!("no free port: {e}"))?
        .local_addr()
        .map(|a| a.port())
        .map_err(|e| e.to_string())
}

fn wait_tcp(port: u16, timeout: Duration) -> bool {
    let start = Instant::now();
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    while start.elapsed() < timeout {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

fn wait_desktop_health(url: &str, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        let out = Command::new("curl")
            .args(["-sf", "--max-time", "1", url])
            .output();
        if let Ok(o) = out {
            if o.status.success() {
                let body = String::from_utf8_lossy(&o.stdout);
                // Docker /health is {"status":"ok"} only. Require the desktop flag
                // so we never treat the compose stack as this .app.
                if body.contains("\"desktop\"") {
                    return true;
                }
            }
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn pid_alive(pid: u32) -> bool {
    Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

fn pid_is_our_app(pid: u32) -> bool {
    let out = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .ok();
    let Some(out) = out else {
        return false;
    };
    if !out.status.success() {
        return false;
    }
    let cmd = String::from_utf8_lossy(&out.stdout).to_ascii_lowercase();
    cmd.contains("sinkduce-desktop") || cmd.contains("sinkduce.app")
}

fn acquire_instance_lock(data_dir: &Path) -> Result<bool, String> {
    fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    let lock = data_dir.join("instance.pid");
    if lock.exists() {
        if let Ok(mut f) = File::open(&lock) {
            let mut buf = String::new();
            if f.read_to_string(&mut buf).is_ok() {
                if let Ok(pid) = buf.trim().parse::<u32>() {
                    if pid_alive(pid) && pid != std::process::id() && pid_is_our_app(pid) {
                        let _ = Command::new("osascript")
                            .args([
                                "-e",
                                &format!("tell application \"{}\" to reopen", app_name()),
                                "-e",
                                &format!("tell application \"{}\" to activate", app_name()),
                            ])
                            .status();
                        return Ok(false);
                    }
                }
            }
        }
    }
    let mut f = File::create(&lock).map_err(|e| e.to_string())?;
    write!(f, "{}", std::process::id()).map_err(|e| e.to_string())?;
    Ok(true)
}

fn user_data_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    PathBuf::from(home)
        .join("Library/Application Support")
        .join(data_dir_name(enterprise_build()))
}

fn read_ui_locale() -> String {
    let path = user_data_dir().join("config.yaml");
    let Ok(text) = fs::read_to_string(path) else {
        return "en".into();
    };
    for line in text.lines() {
        let trimmed = line.trim();
        if let Some(rest) = trimmed.strip_prefix("locale:") {
            let v = rest.trim().trim_matches('"').trim_matches('\'');
            if v == "zh-CN" {
                return "zh-CN".into();
            }
        }
    }
    "en".into()
}

/// 远程模式开关（config.yaml 顶层 ``remote_mode:`` 行，AppConfig 字段）。
/// true = 瘦壳启动：不拉 Qdrant、Python 走 src.remote_shell 入口。
/// 只认显式 ``true``（yaml.dump 的小写形式）；文件缺失/字段缺失 = 关闭，
/// 即默认行为与现状零差异。只匹配顶层键（第 0 列），避免误读嵌套同名键。
fn read_remote_mode() -> bool {
    let path = user_data_dir().join("config.yaml");
    let Ok(text) = fs::read_to_string(path) else {
        return false;
    };
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("remote_mode:") {
            let v = rest.trim();
            return v == "true" || v == "True";
        }
    }
    false
}

fn tray_labels(locale: &str) -> (String, String) {
    let name = app_name();
    if locale == "zh-CN" {
        (format!("显示 {name}"), format!("退出 {name}"))
    } else {
        (format!("Show {name}"), format!("Quit {name}"))
    }
}

fn build_tray_menu<R: tauri::Runtime, M: tauri::Manager<R>>(
    app: &M,
    locale: &str,
) -> Result<Menu<R>, Box<dyn std::error::Error>> {
    let (show_l, quit_l) = tray_labels(locale);
    let show = MenuItem::with_id(app, "show", show_l, true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", quit_l, true, None::<&str>)?;
    Ok(Menu::with_items(app, &[&show, &quit])?)
}

#[tauri::command]
fn set_ui_locale(app: AppHandle, locale: String) {
    let loc = if locale == "zh-CN" { "zh-CN" } else { "en" };
    if let Ok(menu) = build_tray_menu(&app, loc) {
        if let Ok(guard) = app.state::<TrayState>().0.lock() {
            if let Some(tray) = guard.as_ref() {
                let _ = tray.set_menu(Some(menu));
            }
        }
    }
}

fn log_stdio(path: &Path) -> Result<Stdio, String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(Stdio::from(file))
}

fn append_desktop_log(msg: &str) {
    let path = user_data_dir().join("logs/desktop.log");
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(f, "{msg}");
    }
}

fn resource_root(handle: &AppHandle) -> Result<PathBuf, String> {
    if let Ok(p) = std::env::var("SINKDUCE_DESKTOP_RESOURCES") {
        return Ok(PathBuf::from(p));
    }
    let dir = handle.path().resource_dir().map_err(|e| e.to_string())?;
    let nested = dir.join("resources");
    if nested.is_dir() {
        return Ok(nested);
    }
    Ok(dir)
}

/// Finder-launched apps inherit a 256-file soft limit. Qdrant needs more.
fn raise_nofile_limit() {
    unsafe {
        let mut lim = libc::rlimit {
            rlim_cur: 0,
            rlim_max: 0,
        };
        if libc::getrlimit(libc::RLIMIT_NOFILE, &mut lim) != 0 {
            return;
        }
        let want = 10_240;
        let hard = lim.rlim_max;
        let target = if hard == libc::RLIM_INFINITY || hard >= want {
            want
        } else {
            hard
        };
        if lim.rlim_cur < target {
            lim.rlim_cur = target;
            let _ = libc::setrlimit(libc::RLIMIT_NOFILE, &lim);
        }
        append_desktop_log(&format!(
            "nofile soft={} hard={}",
            lim.rlim_cur, lim.rlim_max
        ));
    }
}

fn write_qdrant_config(data_dir: &Path, http_port: u16, grpc_port: u16) -> Result<PathBuf, String> {
    let storage = data_dir.join("qdrant");
    let snapshots = storage.join("snapshots");
    fs::create_dir_all(&snapshots).map_err(|e| e.to_string())?;
    let path = data_dir.join("qdrant-config.yaml");
    // Do not use `\n\` line continuations — Rust strips following indent and
    // produced `service:` as a null key, which qdrant 1.13 rejects.
    let body = format!(
        "log_level: INFO\nstorage:\n  storage_path: {storage}\n  snapshots_path: {snapshots}\nservice:\n  host: 127.0.0.1\n  http_port: {http_port}\n  grpc_port: {grpc_port}\n  enable_cors: true\ntelemetry_disabled: true\n",
        storage = storage.display(),
        snapshots = snapshots.display(),
    );
    fs::write(&path, body).map_err(|e| e.to_string())?;
    Ok(path)
}

fn start_qdrant(
    bin: &Path,
    data_dir: &Path,
    http_port: u16,
    grpc_port: u16,
) -> Result<Child, String> {
    if !bin.is_file() {
        return Err(format!("qdrant missing: {}", bin.display()));
    }
    let storage = data_dir.join("qdrant");
    let snapshots = storage.join("snapshots");
    fs::create_dir_all(&storage).map_err(|e| e.to_string())?;
    fs::create_dir_all(&snapshots).map_err(|e| e.to_string())?;
    let cfg = write_qdrant_config(data_dir, http_port, grpc_port)?;
    let stdout = log_stdio(&data_dir.join("logs/qdrant.log"))?;
    let stderr = log_stdio(&data_dir.join("logs/qdrant.log"))?;
    // Finder-launched apps have cwd=/, which is read-only. Stay in data_dir.
    Command::new(bin)
        .current_dir(data_dir)
        .arg("--config-path")
        .arg(&cfg)
        .arg("--disable-telemetry")
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .map_err(|e| format!("start qdrant: {e}"))
}

#[allow(clippy::too_many_arguments)]
fn start_python(
    py: &Path,
    app_root: &Path,
    data_dir: &Path,
    api_port: u16,
    qdrant_port: u16,
    ffmpeg: Option<&Path>,
    sys_audio: &AudioHelper,
    remote_shell: bool,
) -> Result<Child, String> {
    if !py.is_file() {
        return Err(format!("python missing: {}", py.display()));
    }
    let stdout = log_stdio(&data_dir.join("logs/backend.log"))?;
    let stderr = log_stdio(&data_dir.join("logs/backend.log"))?;
    let mut cmd = Command::new(py);
    if let Some(home) = py.parent().and_then(|p| p.parent()) {
        cmd.env("PYTHONHOME", home);
    }
    cmd.current_dir(app_root)
        .env("PYTHONPATH", app_root)
        .env("PYTHONUNBUFFERED", "1")
        // 字节码缓存重定向到 bundle 外：CPython 默认把 __pycache__ 写进
        // app 包内部（pack.sh 刻意不打 pyc，每次启动必写）→ 每次运行后
        // codesign 密封必然失效 → Dock 图标缓存被刷成白板（2026-09-22
        // 实锤：连 Sep 20 的 /Applications 拷贝都是这个死法）。重定向后
        // 缓存照常跨启动复用，bundle 永远密封。
        .env(
            "PYTHONPYCACHEPREFIX",
            data_dir.join("pycache"),
        )
        .env("SINKDUCE_DATA", data_dir);
    sys_audio.configure_backend(&mut cmd);
    if remote_shell {
        // 远程模式瘦壳：永远只绑回环（本机无内容可分享，LAN 不应触达）。
        // SINKDUCE_REMOTE_SHELL 让 Python 侧裁剪端点面、跳过 services 初始化。
        cmd.env("SINKDUCE_HOST", "127.0.0.1")
            .env("SINKDUCE_REMOTE_SHELL", "1");
    } else {
        // 默认对局域网开放（0.0.0.0）：本机经回环免钥匙，其他设备需配对钥匙。
        // 只想本机可用时设 SINKDUCE_BIND=127.0.0.1。
        cmd.env(
            "SINKDUCE_HOST",
            std::env::var("SINKDUCE_BIND").unwrap_or_else(|_| "0.0.0.0".into()),
        )
        .env("SINKDUCE_QDRANT_HOST", "127.0.0.1")
        .env("SINKDUCE_QDRANT_PORT", qdrant_port.to_string());
    }
    cmd.env("SINKDUCE_PORT", api_port.to_string())
        .env("SINKDUCE_DESKTOP", "1")
        .env("OMP_NUM_THREADS", "1")
        .env("VECLIB_MAXIMUM_THREADS", "1")
        .env("OPENBLAS_NUM_THREADS", "1")
        .env("MKL_NUM_THREADS", "1")
        .env("HF_HOME", data_dir.join("models"))
        .env(
            "SINKDUCE_FRONTEND_DIST",
            app_root.join("frontend").join("dist"),
        )
        .arg("-m")
        .arg(if remote_shell {
            "src.remote_shell"
        } else {
            "src.main"
        })
        .stdout(stdout)
        .stderr(stderr);
    if !remote_shell {
        office_runtime::configure_backend(&mut cmd, app_root);
        // 瘦壳用不到 ffmpeg（转写都在远端盒子做）。
        if let Some(ff) = ffmpeg {
            if ff.is_file() {
                cmd.env("SINKDUCE_FFMPEG", ff);
            }
        }
    }
    cmd.spawn().map_err(|e| format!("start python: {e}"))
}

fn sysaudio_bin(resources: &Path) -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sibling = dir.join("sinkduce-sysaudio");
            if sibling.is_file() {
                return sibling;
            }
        }
    }
    let nested = resources.join("sysaudio");
    if nested.is_file() {
        return nested;
    }
    resources.join("sinkduce-sysaudio")
}

fn start_sysaudio(bin: &Path, port: u16, data_dir: &Path) -> Result<Child, String> {
    if !bin.is_file() {
        return Err(format!("sysaudio missing: {}", bin.display()));
    }
    let stdout = log_stdio(&data_dir.join("logs/sysaudio.log"))?;
    let stderr = log_stdio(&data_dir.join("logs/sysaudio.log"))?;
    Command::new(bin)
        .arg("--port")
        .arg(port.to_string())
        .stdout(stdout)
        .stderr(stderr)
        .spawn()
        .map_err(|e| format!("start sysaudio: {e}"))
}

fn wait_http_ok(url: &str, timeout: Duration) -> bool {
    let start = Instant::now();
    while start.elapsed() < timeout {
        let out = Command::new("curl")
            .args(["-sf", "--max-time", "1", url])
            .output();
        if let Ok(o) = out {
            if o.status.success() {
                return true;
            }
        }
        thread::sleep(Duration::from_millis(80));
    }
    false
}

fn show_error(handle: &AppHandle, err: &str) {
    append_desktop_log(&format!("ERROR {err}"));
    if let Some(win) = handle.get_webview_window("main") {
        let safe = err.replace('\\', "\\\\").replace('\'', "’").replace('\n', " ");
        let _ = win.eval(&format!(
            "document.body.innerHTML='<main style=\"font:14px system-ui;padding:40px;max-width:40rem\">{safe}<p style=\"color:#6a706a;margin-top:1rem\">See ~/Library/Application Support/{}/logs/</p></main>'",
            data_dir_name(enterprise_build())
        ));
    }
}

fn fit_to_screen(handle: &AppHandle) {
    let Some(win) = handle.get_webview_window("main") else {
        return;
    };
    let _ = win.set_fullscreen(false);
    if let Ok(Some(monitor)) = win.current_monitor() {
        // Work area = screen minus menu bar and Dock. Stay a normal window.
        let area = monitor.work_area();
        let _ = win.set_position(area.position);
        let _ = win.set_size(area.size);
    }
}

fn show_main(handle: &AppHandle) {
    if let Some(win) = handle.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

fn install_tray(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let locale = read_ui_locale();
    let menu = build_tray_menu(app, &locale)?;
    let template_icon = tauri::image::Image::from_bytes(TRAY_TEMPLATE_PNG)?;
    let tray = TrayIconBuilder::new()
        .menu(&menu)
        .icon(template_icon)
        .icon_as_template(true)
        .tooltip(app_name())
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show_main(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        });
    let icon = tray.build(app)?;
    if let Ok(mut guard) = app.state::<TrayState>().0.lock() {
        *guard = Some(icon);
    }
    Ok(())
}

fn open_ui(handle: &AppHandle, port: u16) {
    // Unique query so WKWebView cannot reuse a cached SPA index.html.
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(1);
    let url = format!("http://127.0.0.1:{port}/?v={stamp}");
    append_desktop_log(&format!("ready {url}"));
    if let Some(win) = handle.get_webview_window("main") {
        let escaped = url.replace('\\', "\\\\").replace('\'', "\\'");
        let _ = win.eval(&format!("window.location.replace('{escaped}')"));
    }
    // app.restart() 重启出的实例不保证被 macOS 自动激活（2026-09-22 实测：经
    // 「立即重启」重启后窗口不是 key window，第一次点击被系统吃掉用于激活，
    // 表现为「点击没反应、连点才有反应」）。显式 show + 置焦把窗口带成 key。
    show_main(handle);
}

// ── In-app updater ──────────────────────────────────────────────────────────
//
// Updates are downloaded by this process and the bundle is swapped by
// tauri-plugin-updater, so nothing written ever carries the com.apple.quarantine
// attribute and Gatekeeper never assesses the new version (browser downloads do
// stamp it, which is why DMG updates hit "Open Anyway" every release).

/// Progress reported to the SPA via updater_status polling.
#[derive(Clone, serde::Serialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
enum UpdaterPhase {
    Idle,
    Checking,
    Downloading {
        received: u64,
        total: Option<u64>,
    },
    Installing,
    Ready {
        version: String,
    },
    /// Endpoint reachable but no newer version — the SPA only opens the dialog
    /// when its own latest.json check saw one, so this is an anomaly worth naming.
    NoneAvailable,
    Failed {
        message: String,
    },
}

struct UpdaterState(std::sync::Arc<std::sync::Mutex<UpdaterPhase>>);

fn set_phase(state: &std::sync::Arc<std::sync::Mutex<UpdaterPhase>>, phase: UpdaterPhase) {
    if let Ok(mut guard) = state.lock() {
        *guard = phase;
    }
}

/// The running .app bundle (…/SinkDuce.app) — the directory the updater swaps.
fn app_bundle_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    exe.ancestors()
        .find(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("app")))
        .map(Path::to_path_buf)
}

/// The updater renames the bundle out of its parent dir; if that dir is not
/// writable the install can only succeed via an admin prompt, which we refuse —
/// the SPA falls back to the DMG instead.
fn bundle_parent_writable(bundle: &Path) -> bool {
    let Some(parent) = bundle.parent() else {
        return false;
    };
    let probe = parent.join(format!(".sinkduce-write-probe-{}", std::process::id()));
    match File::create(&probe) {
        Ok(_) => {
            let _ = fs::remove_file(&probe);
            true
        }
        Err(_) => false,
    }
}

async fn run_updater_flow(
    app: &AppHandle,
    state: &std::sync::Arc<std::sync::Mutex<UpdaterPhase>>,
) -> Result<(), String> {
    if enterprise_build() {
        return Err("Enterprise updates are unavailable until a separate channel is configured".into());
    }
    {
        let guard = state.lock().expect("updater lock");
        if matches!(
            *guard,
            UpdaterPhase::Checking | UpdaterPhase::Downloading { .. } | UpdaterPhase::Installing
        ) {
            return Ok(());
        }
    }
    let Some(bundle) = app_bundle_dir() else {
        let msg: String = "cannot locate .app bundle".into();
        set_phase(state, UpdaterPhase::Failed { message: msg.clone() });
        return Err(msg);
    };
    if !bundle_parent_writable(&bundle) {
        append_desktop_log(&format!("updater: {} not writable", bundle.display()));
        set_phase(
            state,
            UpdaterPhase::Failed {
                message: "not-writable".into(),
            },
        );
        return Err("not-writable".into());
    }

    set_phase(state, UpdaterPhase::Checking);
    let mut builder = app.updater_builder();
    // SINKDUCE_UPDATER_ENDPOINT overrides the compiled-in R2 endpoint — used to
    // test updates against a local server (SINKDUCE_UPDATER_AUTO=1 drives it).
    // Local test servers run self-signed TLS, so cert validation is relaxed in
    // that mode only; production (no env) keeps the plugin's strict defaults.
    if let Ok(ep) = std::env::var("SINKDUCE_UPDATER_ENDPOINT") {
        builder = builder.configure_client(|b| b.danger_accept_invalid_certs(true));
        let url: url::Url = ep
            .parse()
            .map_err(|e| format!("bad SINKDUCE_UPDATER_ENDPOINT: {e}"))?;
        builder = builder
            .endpoints(vec![url])
            .map_err(|e| format!("bad SINKDUCE_UPDATER_ENDPOINT: {e}"))?;
    }
    let updater = builder.build().map_err(|e| e.to_string())?;
    let update = match updater.check().await.map_err(|e| e.to_string())? {
        Some(update) => update,
        None => {
            append_desktop_log(&format!(
                "updater: endpoint has no newer version (installed v{})",
                app.package_info().version
            ));
            set_phase(state, UpdaterPhase::NoneAvailable);
            return Ok(());
        }
    };
    let version = update.version.clone();
    append_desktop_log(&format!("updater: downloading {version}"));
    set_phase(
        state,
        UpdaterPhase::Downloading {
            received: 0,
            total: None,
        },
    );
    let progress_state = state.clone();
    let install_state = state.clone();
    let result = update
        .download_and_install(
            move |chunk, total| {
                if let Ok(mut guard) = progress_state.lock() {
                    let (received, known_total) = match &*guard {
                        UpdaterPhase::Downloading { received, total } => (*received, *total),
                        _ => (0, None),
                    };
                    *guard = UpdaterPhase::Downloading {
                        received: received + chunk as u64,
                        total: total.or(known_total),
                    };
                }
            },
            move || set_phase(&install_state, UpdaterPhase::Installing),
        )
        .await;
    if let Err(err) = result {
        append_desktop_log(&format!("updater: install failed: {err}"));
        set_phase(
            state,
            UpdaterPhase::Failed {
                message: err.to_string(),
            },
        );
        return Err(err.to_string());
    }
    append_desktop_log(&format!("updater: {version} installed, ready to restart"));
    set_phase(state, UpdaterPhase::Ready { version });
    Ok(())
}

/// Kill sidecars while this process still owns them and release the instance
/// lock, so the freshly spawned binary boots clean instead of hitting
/// "already-running" against our dying pid.
fn restart_after_update(app: &AppHandle) -> ! {
    if let Some(sidecars) = app.try_state::<Arc<Sidecars>>() {
        sidecars.kill_all();
    }
    let _ = fs::remove_file(user_data_dir().join("instance.pid"));
    append_desktop_log("updater: restart");
    app.restart()
}

#[tauri::command]
fn updater_status(state: tauri::State<'_, UpdaterState>) -> UpdaterPhase {
    state.0.lock().expect("updater lock").clone()
}

#[tauri::command]
fn updater_begin(app: AppHandle, state: tauri::State<'_, UpdaterState>) -> Result<(), String> {
    let state = state.0.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(err) = run_updater_flow(&app, &state).await {
            append_desktop_log(&format!("updater: {err}"));
        }
    });
    Ok(())
}

#[tauri::command]
fn updater_restart(app: AppHandle) {
    restart_after_update(&app)
}

/// 前端「立即重启」（远程模式开关切换后生效）。
/// 复用 restart_after_update 的收尾：先杀 sidecars + 释放实例锁，
/// 否则新进程会撞 already-running。
#[tauri::command]
fn restart_app(app: AppHandle) {
    restart_after_update(&app)
}

fn boot(handle: &AppHandle, sidecars: &Sidecars) -> Result<u16, String> {
    raise_nofile_limit();
    let data_dir = user_data_dir();
    if !acquire_instance_lock(&data_dir)? {
        return Err("already-running".into());
    }

    let resources = resource_root(handle)?;
    append_desktop_log(&format!("resources {}", resources.display()));
    let remote_mode = read_remote_mode();
    append_desktop_log(&format!("remote_mode={remote_mode}"));
    // Never steal Docker's 18900 (IPv6 listener is invisible to an IPv4 bind check).
    let api_port = pick_port(preferred_ports(enterprise_build()))?;
    let qdrant_http = pick_port(if enterprise_build() {
        &[16433, 16434, 16435, 16436]
    } else {
        &[16333, 16334, 16335, 16336]
    })?;
    let qdrant_grpc = pick_port(if enterprise_build() {
        &[16437, 16438, 16439, 16440]
    } else {
        &[16337, 16338, 16339, 16340]
    })?;
    let sysaudio_port = pick_port(if enterprise_build() {
        &[18960, 18961, 18962, 18963, 18964]
    } else {
        &[18950, 18951, 18952, 18953, 18954]
    })?;
    append_desktop_log(&format!(
        "ports api={api_port} qdrant_http={qdrant_http} qdrant_grpc={qdrant_grpc} sysaudio={sysaudio_port}"
    ));

    let mut helper = AudioHelper {
        path: sysaudio_bin(&resources),
        url: format!("http://127.0.0.1:{sysaudio_port}"),
        pid: None,
    };
    append_desktop_log(&format!("sysaudio helper {}", helper.path.display()));
    match start_sysaudio(&helper.path, sysaudio_port, &data_dir) {
        Ok(child) => {
            helper.pid = Some(child.id());
            sidecars.push(child);
            let health = format!("http://127.0.0.1:{sysaudio_port}/health");
            if wait_http_ok(&health, Duration::from_secs(5)) {
                append_desktop_log(&format!("sysaudio ready {}", helper.url));
            } else {
                append_desktop_log(&format!("sysaudio did not become ready at {health}"));
            }
        }
        Err(err) => append_desktop_log(&format!("sysaudio skipped: {err}")),
    }

    // 远程模式：Qdrant 整个不启动（瘦壳没有本地库可伺服）；健康检查不变，
    // 瘦壳的 /health 同样带 desktop:true（SINKDUCE_DESKTOP=1 照常注入）。
    if !remote_mode {
        let qdrant = start_qdrant(
            &resources.join("qdrant"),
            &data_dir,
            qdrant_http,
            qdrant_grpc,
        )?;
        sidecars.push(qdrant);

        if !wait_tcp(qdrant_http, Duration::from_secs(20)) {
            let hint = fs::read_to_string(data_dir.join("logs/qdrant.log"))
                .ok()
                .and_then(|t| t.lines().rev().find(|l| !l.trim().is_empty()).map(str::to_string))
                .unwrap_or_else(|| "see logs/qdrant.log".into());
            return Err(format!(
                "Qdrant did not become ready on 127.0.0.1:{qdrant_http} ({hint})"
            ));
        }
    }

    let py = resources.join("python/bin/python");
    let py = if py.is_file() {
        py
    } else {
        resources.join("python/bin/python3")
    };
    let child = start_python(
        &py,
        &resources.join("app"),
        &data_dir,
        api_port,
        qdrant_http,
        Some(&resources.join("ffmpeg")),
        &helper,
        remote_mode,
    )?;
    sidecars.push(child);

    let health = format!("http://127.0.0.1:{api_port}/health");
    if !wait_desktop_health(&health, Duration::from_secs(180)) {
        return Err(format!(
            "backend did not become ready at {health} (need desktop:true, not Docker)"
        ));
    }
    Ok(api_port)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(TrayState(Mutex::new(None)))
        .manage(UpdaterState(Arc::new(Mutex::new(UpdaterPhase::Idle))))
        .invoke_handler(tauri::generate_handler![
            set_ui_locale,
            updater_status,
            updater_begin,
            updater_restart,
            restart_app
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            fit_to_screen(&handle);
            if let Err(err) = install_tray(app) {
                append_desktop_log(&format!("tray failed: {err}"));
            }
            let sidecars = Arc::new(Sidecars::new());
            app.manage(sidecars.clone());
            thread::spawn(move || match boot(&handle, &sidecars) {
                Ok(port) => {
                    open_ui(&handle, port);
                    // Headless self-update exercise: SINKDUCE_UPDATER_AUTO=1 plus
                    // SINKDUCE_UPDATER_ENDPOINT lets a packaged .app prove the
                    // download → swap → restart loop without touching the UI.
                    if std::env::var("SINKDUCE_UPDATER_AUTO").as_deref() == Ok("1") {
                        let handle = handle.clone();
                        tauri::async_runtime::spawn(async move {
                            let state = handle.state::<UpdaterState>().0.clone();
                            if let Err(err) = run_updater_flow(&handle, &state).await {
                                append_desktop_log(&format!("auto updater: {err}"));
                            }
                            loop {
                                let phase = state.lock().expect("updater lock").clone();
                                match phase {
                                    UpdaterPhase::Ready { .. } => {
                                        // Give the log a beat to flush, then swap.
                                        thread::sleep(Duration::from_secs(2));
                                        restart_after_update(&handle);
                                    }
                                    UpdaterPhase::Failed { message } => {
                                        append_desktop_log(&format!(
                                            "auto updater: no restart ({message})"
                                        ));
                                        break;
                                    }
                                    UpdaterPhase::NoneAvailable => {
                                        append_desktop_log(
                                            "auto updater: no restart (endpoint has no newer version)",
                                        );
                                        break;
                                    }
                                    _ => thread::sleep(Duration::from_millis(500)),
                                }
                            }
                        });
                    }
                }
                Err(err) if err == "already-running" => handle.exit(0),
                Err(err) => show_error(&handle, &err),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // Red traffic light: hide to menu bar, keep Qdrant/API running.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building SinkDuce")
        .run(|app, event| {
            match event {
                RunEvent::Exit | RunEvent::ExitRequested { .. } => {
                    if let Some(sidecars) = app.try_state::<Arc<Sidecars>>() {
                        sidecars.kill_all();
                    }
                }
                RunEvent::Reopen { has_visible_windows, .. } => {
                    if !has_visible_windows {
                        show_main(app);
                    }
                }
                _ => {}
            }
        });
}

#[cfg(test)]
mod edition_isolation_tests {
    use super::*;

    #[test]
    fn editions_have_distinct_data_directories() {
        assert_eq!(data_dir_name(false), "SinkDuce");
        assert_eq!(data_dir_name(true), "SinkDuce-EE");
    }

    #[test]
    fn editions_prefer_disjoint_ports() {
        assert!(preferred_ports(false)
            .iter()
            .all(|port| !preferred_ports(true).contains(port)));
    }
}
