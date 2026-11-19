# SinkDuce macOS desktop

Source lives in git. Gitignored: build artifacts (`desktop/dist/`, `.cache/`,
`tauri/src-tauri/target/`), downloaded binaries, `r2.env`.
Not part of the Docker product.

## One-click export

From the repo root, on Apple Silicon:

```bash
./desktop/package_macos.sh
```

Official release (same tag as Docker / GitHub Release):

```bash
git checkout vX.Y.Z
./desktop/package_macos.sh
./desktop/desktop_release upload
```

`upload` pushes the DMG to the GitHub Release **and** to Cloudflare R2
(bucket `sinkduce-releases`, public at https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev —
DMG, checksum, signed updater archive, signature, and `latest.json`). R2 needs a one-time `desktop/r2.env`
(copy from `desktop/r2.env.template`, paste an R2 API token's S3 keys).
Retry R2 only: `./desktop/desktop_release upload-r2`.

Local packs that are not a tagged release: `SINKDUCE_PACK_DEV=1 ./desktop/package_macos.sh`.

Community output: `desktop/dist/SinkDuce.app`, `desktop/dist/SinkDuce.dmg`, and
`desktop/dist/SinkDuce-macos-arm64-vX.Y.Z.app.tar.gz`.
The public Community build includes all retained role, ACL, and device-key
management features under the same AGPL license. Its app identity and update
channel remain Community.

For a local enterprise pack, use
`SINKDUCE_PACK_DEV=1 SINKDUCE_EDITION=enterprise ./desktop/package_macos.sh`.
It produces `SinkDuce-EE.app`, `SinkDuce-EE.dmg`, and
`SinkDuce-EE-macos-arm64-vX.Y.Z.app.tar.gz` in `desktop/dist/`.
The enterprise app has its own bundle identifier and data directory, so both
editions can run at the same time. Enterprise update prompts and in-app updates
are disabled until an enterprise release channel exists. Full Community loads
existing permission settings through the same retained management modules.

First run downloads Qdrant v1.13.2 and may take several minutes (frontend build + Python venv + Tauri compile).

## Signed desktop installer

Run the installer from the project repository.

```bash
curl -fsSL https://raw.githubusercontent.com/superdd-coder/SinkDuce/main/desktop/install.sh | sh
```

The script verifies the signed updater archive using the public key embedded in
the desktop updater. It downloads a checksum-pinned Minisign verifier into a
temporary directory, checks the app identity and version, and stages the new
bundle beside the installed app before replacement. Non-admin users can
install into their own Applications directory.

## What the .app does

1. Picks free local ports (skips 18900 if Docker is using it)
2. Starts bundled Qdrant
3. Starts bundled Python / FastAPI with `SINKDUCE_*`
4. Opens a window on `http://127.0.0.1:<port>`
5. Stops both processes on quit

Community data: `~/Library/Application Support/SinkDuce`.
Enterprise data: `~/Library/Application Support/SinkDuce-EE`.
Existing data stays in its current directory. Full Community can use its
existing directory even when it contains custom roles and ACL rules. The two
directories are kept separate; moving data between them is an explicit user
operation, with a backup retained until the destination app has been checked.
Models still download in-app to that folder. They are not inside the `.app`.

## After export

- A local build may require approval in **System Settings → Privacy & Security** on first launch.
- Do not run the .app and Docker `app` against the same data directory
- Microphone permission is requested the first time you live-transcribe
- Meeting recordings use an existing ScreenCaptureKit grant for system audio, with an audio-only Core Audio fallback. To enable ScreenCaptureKit, add the installed SinkDuce app in **System Settings → Privacy & Security → Screen & System Audio Recording**.
- Closing the window hides to the menu-bar extra; Quit from the extra or press Cmd+Q to stop Qdrant and the API.
- `package_macos.sh` also writes `desktop/dist/SinkDuce.dmg` (app + Applications shortcut). Unsigned builds hit Gatekeeper on browser download — public distribution should point users at the one-line installer; a real fix needs an Apple Developer ID + notarization.
