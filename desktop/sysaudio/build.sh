#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PLIST="Info.plist"
if [[ "${SINKDUCE_EDITION:-community}" == "enterprise" ]]; then
  PLIST="../.cache/sysaudio-ee-Info.plist"
  mkdir -p ../.cache
  cp Info.plist "$PLIST"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier app.sinkduce.desktop.ee.sysaudio' "$PLIST"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleName SinkDuce-EE' "$PLIST"
  /usr/libexec/PlistBuddy -c 'Set :CFBundleDisplayName SinkDuce-EE' "$PLIST"
fi
swiftc -O -swift-version 5 -parse-as-library \
  -target arm64-apple-macosx14.4 \
  -framework AVFoundation \
  -framework AppKit \
  -framework CoreAudio \
  -framework AudioToolbox \
  -framework Foundation \
  -framework ScreenCaptureKit \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$PLIST" \
  -o sinkduce-sysaudio \
  main.swift CaptureState.swift
chmod 755 sinkduce-sysaudio
echo "built $(pwd)/sinkduce-sysaudio"
