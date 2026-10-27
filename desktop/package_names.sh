#!/usr/bin/env bash
# Shared output names for the macOS packager. Source this file, then call package_names.

package_names() {
  local edition="$1" version="$2" product
  case "$edition" in
    community) product="SinkDuce" ;;
    enterprise) product="SinkDuce-EE" ;;
    *) echo "unknown SINKDUCE_EDITION: $edition" >&2; return 1 ;;
  esac
  APP_NAME="${product}.app"
  DMG_NAME="${product}.dmg"
  TARGZ_NAME="${product}-macos-arm64-v${version}.app.tar.gz"
}
