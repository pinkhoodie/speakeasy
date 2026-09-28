#!/usr/bin/env bash
# Build dist/Speakeasy.app with the WebRTC framework embedded.
#
#   SPEAKEASY_CODESIGN_IDENTITY  signing identity (default: "-" = ad-hoc). A stable identity keeps
#                                macOS microphone consent across rebuilds; ad-hoc builds may re-prompt.
#   SPEAKEASY_BUNDLE_ID          bundle identifier override (default: co.speakeasy.mac).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
swift build -c release
BIN_DIR="$(swift build -c release --show-bin-path)"
APP="$ROOT/dist/Speakeasy.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"
cp "$BIN_DIR/Speakeasy" "$APP/Contents/MacOS/Speakeasy"
cp "$ROOT/Resources/Info.plist" "$APP/Contents/Info.plist"
cp "$ROOT/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
# Voice preview clips played from Settings.
ditto "$ROOT/Resources/VoiceSamples" "$APP/Contents/Resources/VoiceSamples"
chmod +x "$APP/Contents/MacOS/Speakeasy"
if [[ -n "${SPEAKEASY_BUNDLE_ID:-}" ]]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $SPEAKEASY_BUNDLE_ID" "$APP/Contents/Info.plist"
fi

# Native call engine: embed the WebRTC binary framework (stasel/WebRTC via SwiftPM).
WEBRTC_SRC=""
for candidate in \
    "$BIN_DIR/WebRTC.framework" \
    "$ROOT/.build/artifacts/webrtc/WebRTC/WebRTC.xcframework/macos-x86_64_arm64/WebRTC.framework"; do
    if [[ -d "$candidate" ]]; then WEBRTC_SRC="$candidate"; break; fi
done
if [[ -z "$WEBRTC_SRC" ]]; then
    echo "error: WebRTC.framework not found (run swift build first)" >&2
    exit 1
fi
# ditto preserves the versioned-bundle symlinks that codesign requires.
ditto "$WEBRTC_SRC" "$APP/Contents/Frameworks/WebRTC.framework"
# Headers/Modules are build-time only; drop them from the shipped bundle.
FW="$APP/Contents/Frameworks/WebRTC.framework"
rm -rf "$FW/Headers" "$FW/Modules" "$FW/Versions/A/Headers" "$FW/Versions/A/Modules"

# The executable links @rpath/WebRTC.framework/WebRTC; make the bundle self-contained.
if ! otool -l "$APP/Contents/MacOS/Speakeasy" | grep -q "@executable_path/../Frameworks"; then
    install_name_tool -add_rpath "@executable_path/../Frameworks" "$APP/Contents/MacOS/Speakeasy"
fi

SIGNING_IDENTITY="${SPEAKEASY_CODESIGN_IDENTITY:--}"
# Sign inside-out with the same identity: embedded framework first, then the app.
codesign --force --sign "$SIGNING_IDENTITY" "$FW"
codesign --force --sign "$SIGNING_IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"
echo "$APP"
