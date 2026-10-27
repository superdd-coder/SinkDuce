#!/usr/bin/env bash
# Build SinkDuce.app on this Mac. Output: desktop/dist/SinkDuce.app
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SINKDUCE_EDITION="${SINKDUCE_EDITION:-community}"
if [[ "$SINKDUCE_EDITION" != "community" && "$SINKDUCE_EDITION" != "enterprise" ]]; then
  echo "unknown SINKDUCE_EDITION: $SINKDUCE_EDITION" >&2
  exit 1
fi
if [[ "$SINKDUCE_EDITION" == "enterprise" && ! -f "$ROOT/ee/LICENSE" ]]; then
  echo "enterprise source and license are required for an enterprise build" >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "package_macos.sh only supports Apple Silicon macOS." >&2
  exit 1
fi

echo "== 0/6 release gate + version stamp =="
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 \
    && "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "Need Python 3.10+ to stamp the desktop version (python3 on PATH is too old)." >&2
  exit 1
fi
"$PY" "$ROOT/desktop/desktop_release.py" check-git
VERSION="$("$PY" "$ROOT/desktop/desktop_release.py" version)"
source "$ROOT/desktop/package_names.sh"
package_names "$SINKDUCE_EDITION" "$VERSION"
"$PY" "$ROOT/desktop/desktop_release.py" stamp
echo "packaging SinkDuce ${VERSION} ($PY)"

echo "== 1/5 frontend =="
(
  cd frontend
  if [[ ! -d node_modules ]]; then
    pnpm install
  fi
  if [[ "$SINKDUCE_EDITION" == "enterprise" ]]; then
    pnpm exec tsc -p tsconfig.enterprise.json --noEmit
  fi
  pnpm build
)

echo "== 2/5 sidecars =="
chmod +x desktop/sidecars/download.sh desktop/python/pack.sh
desktop/sidecars/download.sh

echo "== 3/5 python payload =="
desktop/python/pack.sh

echo "== 3.5/5 system audio helper =="
chmod +x desktop/sysaudio/build.sh
desktop/sysaudio/build.sh

echo "== 4/5 Tauri shell =="
(
  cd desktop/tauri
  if [[ ! -d node_modules ]]; then
    pnpm install
  fi
  echo "→ generating macOS squircle icons from frontend/public/favicon.png"
  PY=""
  for c in "$ROOT/desktop/.cache/python/bin/python3.12" python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    if [[ -x "$c" ]]; then PY="$c"; break; fi
  done
  "$PY" "$ROOT/desktop/make_app_icon.py"
  if [[ "$SINKDUCE_EDITION" == "enterprise" ]]; then
    pnpm exec tauri build --target aarch64-apple-darwin --bundles app \
      --config '{"productName":"SinkDuce-EE","identifier":"app.sinkduce.desktop.ee"}'
  else
    pnpm exec tauri build --target aarch64-apple-darwin --bundles app
  fi
)

APP="$ROOT/desktop/tauri/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/$APP_NAME"
if [[ ! -d "$APP" ]]; then
  APP="$ROOT/desktop/tauri/src-tauri/target/release/bundle/macos/$APP_NAME"
fi
if [[ ! -d "$APP" ]]; then
  echo "Tauri did not produce a .app" >&2
  exit 1
fi

echo "== 5/5 inject python + qdrant + ffmpeg =="
# Do not let Tauri embed these as resources (too large / permission issues).
RES="$APP/Contents/Resources/resources"
rm -rf "$RES"
mkdir -p "$RES/python" "$RES/app"
# --exclude='.DS_Store': Finder may have dropped one into the caches; it
# must never reach the shipped bundle.
rsync -a --exclude='.DS_Store' "$ROOT/desktop/.cache/python/" "$RES/python/"
rsync -a --exclude='.DS_Store' "$ROOT/desktop/.cache/app/" "$RES/app/"
if [[ "$SINKDUCE_EDITION" == "community" ]]; then
  "$PY" "$ROOT/scripts/check_community_artifact.py" tree "$RES/app" --python-prefix "$RES/python"
fi
cp "$ROOT/desktop/sidecars/qdrant" "$RES/qdrant"
chmod 755 "$RES/qdrant"
if [[ -x "$ROOT/desktop/sidecars/ffmpeg" ]]; then
  cp "$ROOT/desktop/sidecars/ffmpeg" "$RES/ffmpeg"
  chmod 755 "$RES/ffmpeg"
fi
echo "→ bundle the private PDF/Office conversion runtime"
"$PY" "$ROOT/desktop/office_runtime.py" \
  --cache "$ROOT/desktop/.cache/office" --destination "$RES/libreoffice" \
  --frontend-dist "$RES/app/frontend/dist"
if [[ -x "$ROOT/desktop/sysaudio/sinkduce-sysaudio" ]]; then
  cp "$ROOT/desktop/sysaudio/sinkduce-sysaudio" "$APP/Contents/MacOS/sinkduce-sysaudio"
  chmod 755 "$APP/Contents/MacOS/sinkduce-sysaudio"
  cp "$ROOT/desktop/sysaudio/sinkduce-sysaudio" "$RES/sysaudio"
  chmod 755 "$RES/sysaudio"
  # Resources are sealed as data by --deep. Sign both copies explicitly so
  # even older recovery paths retain the initial helper's edition identity.
  SYSAUDIO_ID="app.sinkduce.desktop"
  if [[ "$SINKDUCE_EDITION" == "enterprise" ]]; then
    SYSAUDIO_ID="app.sinkduce.desktop.ee.sysaudio"
  fi
  codesign --force --sign - --identifier "$SYSAUDIO_ID" "$APP/Contents/MacOS/sinkduce-sysaudio"
  codesign --force --sign - --identifier "$SYSAUDIO_ID" "$RES/sysaudio"
fi

apply_app_icon() {
  local dest="$1"
  local icns="$ROOT/desktop/tauri/src-tauri/icons/icon.icns"
  if [[ ! -f "$icns" ]]; then
    echo "missing $icns" >&2
    return 1
  fi
  # New filename each time the icon shape changes — macOS caches by CFBundleIconFile.
  rm -f "$dest/Contents/Resources/icon.icns" \
    "$dest/Contents/Resources/AppIcon.icns" \
    "$dest/Contents/Resources/AppIconMac.icns"
  cp -f "$icns" "$dest/Contents/Resources/AppIconDock.icns"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIconFile AppIconDock' "$dest/Contents/Info.plist" \
    || /usr/libexec/PlistBuddy -c 'Add :CFBundleIconFile string AppIconDock' "$dest/Contents/Info.plist"
  if /usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "$dest/Contents/Info.plist" >/dev/null 2>&1; then
    /usr/libexec/PlistBuddy -c 'Set :CFBundleIconName AppIconDock' "$dest/Contents/Info.plist"
  else
    /usr/libexec/PlistBuddy -c 'Add :CFBundleIconName string AppIconDock' "$dest/Contents/Info.plist"
  fi
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${VERSION}" "$dest/Contents/Info.plist" \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string ${VERSION}" "$dest/Contents/Info.plist"
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${VERSION}" "$dest/Contents/Info.plist" \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${VERSION}" "$dest/Contents/Info.plist"
  touch "$dest" "$dest/Contents/Info.plist" "$dest/Contents/Resources/AppIconDock.icns"
}

echo "→ apply macOS squircle icon"
apply_app_icon "$APP"

# macOS 26 Liquid Glass icon: compiled from desktop/icons/SinkDuce.icon by
# .github/workflows/icon.yml (needs Xcode 26's actool). CFBundleIconName must
# equal the .icon filename stem; CFBundleIconFile above stays AppIconDock so
# macOS < 26 keeps rendering the icns.
LIQUID_GLASS_NAME="SinkDuce"
LIQUID_GLASS_CAR="$ROOT/desktop/icons/Assets.car"
if [[ -f "$LIQUID_GLASS_CAR" ]]; then
  echo "→ apply macOS 26 liquid glass icon (Assets.car)"
  cp -f "$LIQUID_GLASS_CAR" "$APP/Contents/Resources/Assets.car"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconName ${LIQUID_GLASS_NAME}" "$APP/Contents/Info.plist" \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconName string ${LIQUID_GLASS_NAME}" "$APP/Contents/Info.plist"
  touch "$APP" "$APP/Contents/Info.plist"
else
  # Tauri re-links the bundle on every build — drop any stale car so icon
  # presence always mirrors what is in the repo right now.
  rm -f "$APP/Contents/Resources/Assets.car"
  echo "→ no desktop/icons/Assets.car — liquid glass icon skipped (icns fallback)"
fi

codesign --force --deep --sign - "$APP" >/dev/null
codesign --verify --deep --strict "$APP"

echo "→ verify PDF export using only the bundled conversion runtime"
PYTHONHOME="$RES/python" PYTHONPATH="$RES/app" \
  PYTHONPYCACHEPREFIX="$ROOT/desktop/.cache/export-check/pycache" \
  SINKDUCE_DATA="$ROOT/desktop/.cache/export-check" \
  SINKDUCE_LIBREOFFICE="$RES/libreoffice/LibreOffice.app/Contents/MacOS/soffice" \
  "$RES/python/bin/python3.12" "$ROOT/desktop/verify_office_runtime.py"
codesign --verify --deep --strict "$APP"

mkdir -p "$ROOT/desktop/dist"
# Same atomic-replace pattern as pack.sh: dist/ is a folder the user browses,
# so a live Finder view could poison a plain `rm -rf` here too.
DIST_APP="$ROOT/desktop/dist/$APP_NAME"
rm -rf "${DIST_APP}.trash" 2>/dev/null || true
if [[ -e "$DIST_APP" ]]; then
  mv "$DIST_APP" "${DIST_APP}.trash"
  rm -rf "${DIST_APP}.trash" &
fi
cp -R "$APP" "$DIST_APP"

echo "== 6/6 disk image =="
STAGE="$ROOT/desktop/dist/.dmg-stage"
DMG="$ROOT/desktop/dist/$DMG_NAME"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$DIST_APP" "$STAGE/$APP_NAME"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "${APP_NAME%.app}" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"

echo "== 7/7 in-app updater payload =="
# The shell downloads this tar.gz itself and swaps the bundle, so the files on
# disk never carry the browser's quarantine stamp and Gatekeeper stays silent.
# Top-level entry must be the .app bundle — tauri-plugin-updater strips the first
# path component when extracting.
TARGZ="$ROOT/desktop/dist/$TARGZ_NAME"
rm -f "$TARGZ" "$TARGZ.sig"
# COPYFILE_DISABLE + --no-xattrs: the bundle carries xattrs (com.apple.provenance
# et al) which bsdtar would archive as AppleDouble `._SinkDuce.app` entries —
# tauri-plugin-updater chokes on those ("failed to unpack ._SinkDuce.app").
# Codesign signatures live in _CodeSignature/, so dropping xattrs is safe.
( cd "$ROOT/desktop/dist" && COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TARGZ" "$APP_NAME" )
if [[ -n "${TAURI_SIGNING_PRIVATE_KEY:-}" ]]; then
  KEY_ARGS=(-k "$TAURI_SIGNING_PRIVATE_KEY")
elif [[ -f "$ROOT/desktop/updater.key.private" ]]; then
  KEY_ARGS=(-f "$ROOT/desktop/updater.key.private")
elif [[ "${SINKDUCE_PACK_DEV:-}" == "1" ]]; then
  echo "WARN  no updater signing key (TAURI_SIGNING_PRIVATE_KEY / desktop/updater.key.private) — shipped unsigned" >&2
  echo
  echo "OK  $DIST_APP"
  echo "OK  $DMG"
  echo "OK  $TARGZ (unsigned — in-app update cannot install it)"
  exit 0
else
  echo "No updater signing key: set TAURI_SIGNING_PRIVATE_KEY (CI) or keep desktop/updater.key.private (local)." >&2
  exit 1
fi
(
  cd "$ROOT/desktop/tauri"
  TAURI_SIGNING_PRIVATE_KEY_PASSWORD="${TAURI_SIGNING_PRIVATE_KEY_PASSWORD:-}" \
    pnpm exec tauri signer sign "${KEY_ARGS[@]}" "$TARGZ"
)
echo "OK  $TARGZ"
echo "OK  $TARGZ.sig"

echo
echo "OK  $DIST_APP"
echo "OK  $DMG"
echo "Open with:  open \"$DIST_APP\""
