#!/bin/bash
# 热拷开发改动进 desktop/dist/SinkDuce.app（不必整包重打）。
#
# 用法:
#   ./desktop/hotcopy_app.sh             # 拷 src/ + frontend/dist
#   ./desktop/hotcopy_app.sh --binary    # 再拷重编过的壳二进制（先 cargo build --target aarch64-apple-darwin --release）
#
# 铁律：永远以 codesign 收尾。改了 bundle 内容不补签 = 签名失效，
# macOS 给白板图标、Gatekeeper 也可拒绝启动（2026-09-22 踩过：热拷后漏签，
# logo「坏了」）。收尾还会刷 LaunchServices 注册与 Dock 缓存。
set -euo pipefail
cd "$(dirname "$0")/.."
APP=desktop/dist/SinkDuce.app
[ -d "$APP" ] || { echo "no $APP — 先跑 SINKDUCE_PACK_DEV=1 ./desktop/package_macos.sh"; exit 1; }

rsync -a --exclude='__pycache__' --exclude='.DS_Store' \
  src/ "$APP/Contents/Resources/resources/app/src/"
rsync -a --delete --exclude='.DS_Store' \
  frontend/dist/ "$APP/Contents/Resources/resources/app/frontend/dist/"

if [ "${1:-}" = "--binary" ]; then
  BIN=desktop/tauri/src-tauri/target/aarch64-apple-darwin/release/sinkduce-desktop
  [ -f "$BIN" ] || { echo "no $BIN — 先 cargo build --target aarch64-apple-darwin --release"; exit 1; }
  cp "$BIN" "$APP/Contents/MacOS/sinkduce-desktop"
fi

codesign --force --sign - "$APP"
codesign -vv "$APP"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP" 2>/dev/null || true
killall Dock 2>/dev/null || true
echo "hot-copied into $APP"
