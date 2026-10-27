fn main() {
    println!("cargo:rerun-if-env-changed=SINKDUCE_EDITION");
    let edition = std::env::var("SINKDUCE_EDITION").unwrap_or_else(|_| "community".into());
    assert!(
        edition == "community" || edition == "enterprise",
        "invalid SINKDUCE_EDITION"
    );
    println!("cargo:rustc-env=SINKDUCE_DESKTOP_EDITION={edition}");
    // ACL: Tauri v2 denies every app IPC command unless the app manifest
    // declares it AND the capability grants the generated `allow-$command`.
    // Without this, updater_* invokes are rejected at the IPC layer
    // ("Command updater_begin not allowed by ACL") before our handler ever
    // runs — and the failure is invisible in desktop.log. Must stay in sync
    // with capabilities/default.json.
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "updater_begin",
            "updater_status",
            "updater_restart",
            "set_ui_locale",
            "restart_app",
        ])),
    )
    .expect("failed to run tauri-build");
}
