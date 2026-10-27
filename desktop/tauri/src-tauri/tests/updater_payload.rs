//! Verifies the packaged in-app updater payload against the pubkey embedded in
//! tauri.conf.json, using the same minisign-verify calls as
//! tauri-plugin-updater's verify_signature. Skips silently when no pack output
//! exists (CI test runs, fresh clones) — meaningful right after
//! ./desktop/package_macos.sh.

use base64::Engine as _;
use minisign_verify::{PublicKey, Signature};
use std::path::PathBuf;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

fn embedded_pubkey() -> String {
    let conf = std::fs::read_to_string(
        repo_root().join("desktop/tauri/src-tauri/tauri.conf.json"),
    )
    .expect("tauri.conf.json");
    let value: serde_json::Value = serde_json::from_str(&conf).expect("valid conf json");
    value["plugins"]["updater"]["pubkey"]
        .as_str()
        .expect("plugins.updater.pubkey")
        .to_string()
}

fn decode_base64_to_string(data: &str) -> String {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(data.trim())
        .expect("valid base64");
    String::from_utf8(bytes).expect("minisign text format must be utf-8")
}

#[test]
fn updater_payload_signature_matches_embedded_pubkey() {
    let dist = repo_root().join("desktop/dist");
    let Some(tar_gz) = find_latest(&dist, ".app.tar.gz") else {
        eprintln!("skipping: no .app.tar.gz in desktop/dist (run package_macos.sh first)");
        return;
    };
    let sig_path = PathBuf::from(format!("{}.sig", tar_gz.display()));
    if !sig_path.is_file() {
        eprintln!("skipping: unsigned payload {} (PACK_DEV without key)", tar_gz.display());
        return;
    }

    let pubkey_b64 = embedded_pubkey();
    let public_key =
        PublicKey::decode(&decode_base64_to_string(&pubkey_b64)).expect("decode pubkey from tauri.conf.json");
    let sig_b64 = std::fs::read_to_string(&sig_path).expect("read .sig");
    let signature =
        Signature::decode(&decode_base64_to_string(&sig_b64)).expect("decode signature");

    let payload = std::fs::read(&tar_gz).expect("read payload");
    // Same call as tauri-plugin-updater (legacy-compatible verification).
    public_key
        .verify(&payload, &signature, true)
        .expect("payload signature must verify against the embedded pubkey");
}

/// Newest *.app.tar.gz in dist (older versions may linger across releases).
fn find_latest(dir: &std::path::Path, suffix: &str) -> Option<PathBuf> {
    let mut hits: Vec<PathBuf> = std::fs::read_dir(dir)
        .ok()?
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().ends_with(suffix)))
        .collect();
    hits.sort_by_key(|p| p.metadata().and_then(|m| m.modified()).ok());
    hits.pop()
}
