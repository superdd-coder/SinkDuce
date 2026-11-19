#!/bin/sh
# macOS Apple Silicon installer. The public key also ships in Tauri's updater config.
set -u
BASE=https://pub-789a11bb112e4ed59d731554588eebfb.r2.dev
PUBLIC_KEY=RWQEjiXiDsHq3ugOKFkzwIpVsj09YY2QLB01ARB5B7GsZYaaHB0dxYjk
VERIFY_URL=https://github.com/jedisct1/minisign/releases/download/0.12/minisign-0.12-macos.zip
VERIFY_SHA=89000b19535765f9cffc65a65d64a820f433ef6db8020667f7570e06bf6aac63

die() { printf '%s\n' "$*" >&2; exit 1; }
fetch() { curl --proto '=https' --proto-redir '=https' -fsSL --retry 3 --connect-timeout 15 --max-time 1800 -o "$2" "$1"; }
valid_version() {
    case "$1" in ''|*[!0-9.]*) return 1;; esac
    printf '%s\n' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'
}
is_downgrade() {
    awk -v old="$1" -v new="$2" 'BEGIN {
        split(old, a, "."); split(new, b, ".");
        for (i=1; i<=3; i++) { if (a[i]+0 > b[i]+0) exit 0; if (a[i]+0 < b[i]+0) exit 1 }
        exit 1
    }'
}
replace_app() {
    [ -d "$1" ] && [ ! -L "$1" ] && [ ! -e "$3" ] || return 1
    if [ -e "$2" ]; then mv "$2" "$3" || return 1; fi
    if mv "$1" "$2"; then return 0; fi
    if [ -e "$3" ]; then mv "$3" "$2" || return 1; fi
    return 1
}

main() {
    [ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || die "需要 Apple Silicon Mac / Apple Silicon macOS required."
    umask 077
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/sinkduce.XXXXXX") || die "Cannot create temporary directory."
    stage='' dest='' lock=''
    cleanup() {
        if [ -n "$stage" ] && [ -d "$stage" ]; then
            if [ -e "$stage/previous.app" ] && [ ! -e "$dest" ]; then
                mv "$stage/previous.app" "$dest" || { printf 'Previous app retained at %s\n' "$stage/previous.app" >&2; return 1; }
            fi
            rm -rf "$stage"
        fi
        if [ -n "$lock" ]; then rmdir "$lock"; fi
        rm -rf "$tmp"
    }
    trap cleanup EXIT
    trap 'exit 1' HUP INT TERM

    fetch "$BASE/latest.json" "$tmp/latest.json" || die "无法获取版本 / Cannot read release information."
    version=$(/usr/bin/plutil -extract version raw -o - "$tmp/latest.json") || die "Invalid release manifest."
    valid_version "$version" || die "Invalid release version."
    asset="SinkDuce-macos-arm64-v${version}.app.tar.gz"
    printf '下载并验证 SinkDuce %s / Downloading and verifying the publisher signature\n' "$version"
    fetch "$VERIFY_URL" "$tmp/verifier.zip" || die "Cannot download signature verifier."
    printf '%s  %s\n' "$VERIFY_SHA" "$tmp/verifier.zip" | shasum -a 256 -c - >/dev/null || die "Invalid verifier checksum."
    unzip -p "$tmp/verifier.zip" minisign > "$tmp/minisign" && chmod 700 "$tmp/minisign" || die "Cannot prepare verifier."
    fetch "$BASE/$asset.sig" "$tmp/signature.b64" && /usr/bin/base64 -D -i "$tmp/signature.b64" -o "$tmp/signature" || die "缺少有效签名 / Missing publisher signature."
    fetch "$BASE/$asset" "$tmp/archive" || die "下载失败 / Download failed."
    "$tmp/minisign" -Vq -m "$tmp/archive" -x "$tmp/signature" -P "$PUBLIC_KEY" || die "签名校验失败 / Publisher signature verification failed."

    parent=/Applications
    if { [ -d "$HOME/Applications/SinkDuce.app" ] && [ ! -e "$parent/SinkDuce.app" ]; } || [ ! -w "$parent" ]; then
        parent="$HOME/Applications"
        mkdir -p "$parent" || die "Cannot create user Applications directory."
    fi
    mkdir "$parent/.sinkduce-install.lock" 2>/dev/null || die "Another installer or a previous interrupted installation holds $parent/.sinkduce-install.lock"
    lock="$parent/.sinkduce-install.lock"
    dest="$parent/SinkDuce.app"
    stage=$(mktemp -d "$parent/.sinkduce-install.XXXXXX") || die "Cannot stage the app beside its destination."
    tar -xzf "$tmp/archive" -C "$stage" || die "无法解包 / Cannot unpack the signed app."
    plist="$stage/SinkDuce.app/Contents/Info.plist"
    identifier=$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$plist") || die "Missing application metadata."
    actual=$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$plist") || die "Missing application version."
    [ "$identifier" = app.sinkduce.desktop ] && [ "$actual" = "$version" ] || die "Release version or application identity mismatch."
    /usr/bin/codesign --verify "$stage/SinkDuce.app" || die "Application bundle verification failed."
    if [ -d "$dest" ]; then
        installed=$(/usr/bin/plutil -extract CFBundleShortVersionString raw -o - "$dest/Contents/Info.plist") || die "Cannot verify installed version."
        valid_version "$installed" || die "Cannot verify the installed version."
        if is_downgrade "$installed" "$version"; then die "拒绝降级 / Refusing to replace a newer version."; fi
    fi
    if pgrep -x sinkduce-desktop >/dev/null 2>&1; then
        osascript -e 'tell application "SinkDuce" to quit' >/dev/null 2>&1 || die "请退出 SinkDuce 后重试 / Quit SinkDuce and retry."
        attempts=0
        while pgrep -x sinkduce-desktop >/dev/null 2>&1; do
            [ "$attempts" -lt 10 ] || die "请退出 SinkDuce 后重试 / Quit SinkDuce and retry."
            sleep 1
            attempts=$((attempts+1))
        done
    fi
    replace_app "$stage/SinkDuce.app" "$dest" "$stage/previous.app" || die "安装失败 / Application replacement failed."
    printf '已安装 / Installed SinkDuce %s at %s\n' "$version" "$dest"
    open "$dest"
}

main "$@"
