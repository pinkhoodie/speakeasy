#!/usr/bin/env bash
# Build, sign (Developer ID + hardened runtime), notarize and staple a downloadable Speakeasy.dmg.
#
#   scripts/release.sh 0.2.0            build dist/Speakeasy-0.2.0.dmg, notarized and stapled
#   scripts/release.sh 0.2.0 --publish  also create GitHub release v0.2.0 with the dmg (needs gh)
#
# Needs, once per machine:
#   - a "Developer ID Application" certificate in the login keychain
#   - notarization credentials saved as a keychain profile:
#       xcrun notarytool store-credentials speakeasy --apple-id <apple id> --team-id <team id> \
#         --keychain ~/Library/Keychains/login.keychain-db
#     then export SPEAKEASY_NOTARY_KEYCHAIN=~/Library/Keychains/login.keychain-db
# Overrides: SPEAKEASY_DEVELOPER_ID (identity name), SPEAKEASY_NOTARY_PROFILE (default "speakeasy").
set -euo pipefail
VERSION="${1:?usage: scripts/release.sh <version> [--publish]}"
PUBLISH="${2:-}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "error: version must look like 0.2.0" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROFILE="${SPEAKEASY_NOTARY_PROFILE:-speakeasy}"
IDENTITY="${SPEAKEASY_DEVELOPER_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)}"
[[ -n "$IDENTITY" ]] || { echo "error: no Developer ID Application certificate in the keychain" >&2; exit 1; }
# A file keychain (e.g. the login keychain) keeps working while the screen is locked; the default
# data-protection keychain does not, which breaks unattended releases.
NOTARY_ARGS=(--keychain-profile "$PROFILE")
[[ -n "${SPEAKEASY_NOTARY_KEYCHAIN:-}" ]] && NOTARY_ARGS+=(--keychain "$SPEAKEASY_NOTARY_KEYCHAIN")
xcrun notarytool history "${NOTARY_ARGS[@]}" >/dev/null \
    || { echo "error: notary profile '$PROFILE' missing; run xcrun notarytool store-credentials" >&2; exit 1; }

# The version lives in Info.plist; the build number is the commit count so it only goes up.
BUILD="$(git rev-list --count HEAD)"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" Resources/Info.plist
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" Resources/Info.plist

# Build unsigned (ad-hoc), then re-sign inside-out for distribution.
SPEAKEASY_CODESIGN_IDENTITY="-" ./scripts/package-app.sh >/dev/null
APP="$ROOT/dist/Speakeasy.app"
FW="$APP/Contents/Frameworks/WebRTC.framework"
codesign --force --options runtime --timestamp --sign "$IDENTITY" "$FW"
codesign --force --options runtime --timestamp --entitlements Resources/Speakeasy.entitlements \
    --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

# Disk image: the app plus an Applications shortcut to drag onto.
DMG="$ROOT/dist/Speakeasy-$VERSION.dmg"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/Speakeasy.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Speakeasy" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
codesign --force --timestamp --sign "$IDENTITY" "$DMG"

echo "Notarizing (usually a few minutes)…"
xcrun notarytool submit "$DMG" "${NOTARY_ARGS[@]}" --wait --timeout 30m | tee "$STAGE/notary.log"
grep -q "status: Accepted" "$STAGE/notary.log" || { echo "error: notarization was not accepted" >&2; exit 1; }
xcrun stapler staple "$DMG"
spctl -a -t open --context context:primary-signature -v "$DMG"
shasum -a 256 "$DMG" | tee "$DMG.sha256"

if [[ "$PUBLISH" == "--publish" ]]; then
    # Also as a fixed name, so https://github.com/rungmc357/speakeasy/releases/latest/download/Speakeasy.dmg
    # always downloads the newest app directly (the website's Download button).
    # Only the fixed name is uploaded: the website, the in-app updater (which takes the first .dmg it
    # finds) and every download all get "Speakeasy.dmg", never a versioned file next to an old one.
    cp "$DMG" "$ROOT/dist/Speakeasy.dmg"
    (cd "$ROOT/dist" && shasum -a 256 Speakeasy.dmg > Speakeasy.dmg.sha256)
    gh release create "v$VERSION" "$ROOT/dist/Speakeasy.dmg" "$ROOT/dist/Speakeasy.dmg.sha256" --title "Speakeasy $VERSION" \
        --notes "Signed and notarized macOS app (macOS 14+). Open the dmg and drag Speakeasy into Applications."
fi
echo "$DMG"
