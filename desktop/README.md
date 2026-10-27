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
DMG + `.sha256` + `latest.json`). R2 needs a one-time `desktop/r2.env`
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

## One-line installer (quarantine-free first install)

`desktop/install.sh` is uploaded to R2 by the dmg job:

```bash
curl -fsSL https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/install.sh | sh
```

- curl never sets the `com.apple.quarantine` xattr, so Gatekeeper never fires
  on this path — no Open Anyway, no right-click dance, on every macOS version.
- Version-independent: the script resolves the current release from
  `latest.json` on every run. Re-running it upgrades in place (it quits the
  running app first). Only re-upload it when the script itself changes.
- Messages are bilingual (zh + en). Non-admin users fall back to
  `~/Applications` automatically; sha256 is verified before install.

Manual push (no release needed): `wrangler r2 object put sinkduce-releases/install.sh --file desktop/install.sh`

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

- First open: if Gatekeeper blocks a locally built DMG, run
  `xattr -dr com.apple.quarantine /Applications/SinkDuce.app` — right-click →
  Open no longer exists on macOS 15+; end users should use the one-line installer instead
- Do not run the .app and Docker `app` against the same data directory
- Microphone permission is requested the first time you live-transcribe
- Screen Recording permission is required so meeting recordings include system audio (Zoom / Meet / browser playback). macOS will prompt on first record; if denied, enable SinkDuce under System Settings → Privacy & Security → Screen Recording.
- Closing the window hides to the menu-bar extra; Quit from the extra or press Cmd+Q to stop Qdrant and the API.
- `package_macos.sh` also writes `desktop/dist/SinkDuce.dmg` (app + Applications shortcut). Unsigned builds hit Gatekeeper on browser download — public distribution should point users at the one-line installer; a real fix needs an Apple Developer ID + notarization.
