#!/bin/sh
# SinkDuce one-line installer for macOS (Apple Silicon)
#   curl -fsSL https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev/install.sh | sh
#
# Version-independent: resolves the current release from latest.json on every
# run, so a new release never requires touching this script (re-running it
# doubles as a CLI upgrade path). curl never attaches the quarantine xattr,
# so Gatekeeper is never involved on this path.
#
# 提示词中英双语 / All messages are bilingual (Chinese + English).

set -u

BASE="https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev"
APP="SinkDuce"

say()  { printf "\033[0;32m==>\033[0m \033[1m%s\033[0m\n" "$*"; }
info() { printf "    %s\n" "$*"; }
die()  { printf "\n\033[0;31m✗ 安装失败 / Install failed:\033[0m %s\n" "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "此安装器只支持 macOS / This installer supports macOS only."
[ "$(uname -m)" = "arm64" ]  || die "目前只提供 Apple Silicon (M 系列) 版本 / Only Apple Silicon (M-series) builds are available for now."

say "查询最新版本 / Checking for the latest version"
META=$(curl -fsSL --retry 3 "$BASE/latest.json") || die "连不上发布服务器，请检查网络 / Cannot reach the release server, please check your network."
VER=$(printf '%s' "$META" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
[ -n "$VER" ] || die "latest.json 解析失败 / Failed to parse latest.json."
ASSET="SinkDuce-macos-arm64-v${VER}.dmg"
SIZE=$(printf '%s' "$META" | sed -n 's/.*"size"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' | head -n1)
SIZE_NOTE=""
[ -n "$SIZE" ] && SIZE_NOTE="（约 ~$((SIZE / 1024 / 1024)) MB）"
info "SinkDuce v${VER} ${SIZE_NOTE}"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/sinkduce.XXXXXX") || die "创建临时目录失败 / Failed to create a temp directory."
MNT="$TMP/vol"
cleanup() { hdiutil detach "$MNT" -quiet >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

say "下载 DMG / Downloading DMG"
curl -fL --retry 3 -o "$TMP/$ASSET" "$BASE/$ASSET" || die "下载失败，请重试 / Download failed, please retry."

say "校验 sha256 / Verifying sha256"
curl -fsSL -o "$TMP/$ASSET.sha256" "$BASE/$ASSET.sha256" || die "校验文件下载失败 / Could not download the sha256 file."
( cd "$TMP" && shasum -a 256 -c "$ASSET.sha256" >/dev/null ) || die "sha256 不匹配（下载不完整？），请重试 / sha256 mismatch (incomplete download?), please retry."

say "挂载安装镜像 / Mounting disk image"
mkdir -p "$MNT"
hdiutil attach "$TMP/$ASSET" -readonly -nobrowse -quiet -mountpoint "$MNT" || die "DMG 挂载失败 / Failed to mount the DMG."
SRC=$(find "$MNT" -maxdepth 1 -name "$APP.app" | head -n1)
[ -n "$SRC" ] || die "DMG 里找不到 $APP.app / $APP.app not found inside the DMG."

if pgrep -f "SinkDuce.app|sinkduce-desktop" >/dev/null 2>&1; then
  say "正在退出运行中的 SinkDuce / Quitting the running SinkDuce"
  osascript -e 'tell application "SinkDuce" to quit' >/dev/null 2>&1 || true
  sleep 1
  pkill -x "sinkduce-desktop" 2>/dev/null || true
fi

if [ -d "/Applications/$APP.app" ]; then DEST="/Applications/$APP.app"
elif [ -d "$HOME/Applications/$APP.app" ]; then DEST="$HOME/Applications/$APP.app"
else DEST="/Applications/$APP.app"
fi

say "安装到 / Installing to ${DEST%/*}"
rm -rf "$DEST" 2>/dev/null || true
if ! cp -R "$SRC" "$DEST" 2>/dev/null; then
  info "/Applications 无写入权限，改用个人应用目录 / No write access to /Applications, falling back to ~/Applications"
  mkdir -p "$HOME/Applications"
  DEST="$HOME/Applications/$APP.app"
  rm -rf "$DEST" 2>/dev/null || true
  cp -R "$SRC" "$DEST" || die "拷贝失败 / Copy failed."
fi
hdiutil detach "$MNT" -quiet >/dev/null 2>&1 || true

# curl 路径本不带 quarantine，此步纯兜底（例如脚本被别的下载器包装时）
# Pure safety net — curl never adds quarantine in the first place (covers
# exotic wrappers that re-download the DMG through a tagging agent).
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

say "启动 / Launching $APP"
open "$DEST" || info "启动失败，可手动打开 / Launch failed — open it manually: $DEST"

printf "\n\033[0;32m✅ v%s 安装完成：%s\033[0m\n" "$VER" "$DEST"
printf "   以后更新走应用内提示即可；重跑本命令等效升级。\n"
printf "   Future updates come from the in-app updater; re-running this command upgrades too.\n"
