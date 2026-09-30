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

# The newest dated section of CHANGELOG.md, used as the release notes (and so the update prompt).
latest_changes() {
    awk '/^## /{n++} n==1 && !/^## /{print}' "$ROOT/CHANGELOG.md" | sed -e '/./,$!d'
}
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

[[ -r "${SPEAKEASY_SPARKLE_KEY:-$HOME/.config/speakeasy/sparkle-ed-key}" ]] \
    || { echo "error: Sparkle signing key missing (see scripts/release.sh)" >&2; exit 1; }

# The version lives in Info.plist; the build number is the commit count so it only goes up.
BUILD="$(git rev-list --count HEAD)"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" Resources/Info.plist
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $BUILD" Resources/Info.plist

# Build unsigned (ad-hoc), then re-sign inside-out for distribution.
SPEAKEASY_CODESIGN_IDENTITY="-" ./scripts/package-app.sh >/dev/null
APP="$ROOT/dist/Speakeasy.app"
FW="$APP/Contents/Frameworks/WebRTC.framework"
"$ROOT/scripts/sign-sparkle.sh" "$APP/Contents/Frameworks/Sparkle.framework" "$IDENTITY" --options runtime --timestamp
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

# In-app updates: an appcast pointing at this release's dmg, signed with the Sparkle EdDSA key
# (a private key file on the release Mac, default ~/.config/speakeasy/sparkle-ed-key; never in the repo).
# A file, not the keychain: a keychain read can pop a dialog and stall an unattended release.
SIGN_UPDATE="$ROOT/.build/artifacts/sparkle/Sparkle/bin/sign_update"
SPARKLE_KEY="${SPEAKEASY_SPARKLE_KEY:-$HOME/.config/speakeasy/sparkle-ed-key}"
[[ -r "$SPARKLE_KEY" ]] || { echo "error: Sparkle signing key not found at $SPARKLE_KEY" >&2; exit 1; }
SIG_ATTRS="$("$SIGN_UPDATE" --ed-key-file "$SPARKLE_KEY" "$DMG")"   # sparkle:edSignature="…" length="…"
[[ "$SIG_ATTRS" == *edSignature* ]] || { echo "error: could not sign the update (Sparkle key missing?)" >&2; exit 1; }
BUILD_NUM="$(/usr/libexec/PlistBuddy -c "Print :CFBundleVersion" Resources/Info.plist)"
MIN_OS="$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" Resources/Info.plist)"
cat > "$ROOT/dist/appcast.xml" <<XML
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>Speakeasy</title>
    <item>
      <title>Speakeasy $VERSION</title>
      <link>https://github.com/rungmc357/speakeasy/releases/tag/v$VERSION</link>
      <sparkle:version>$BUILD_NUM</sparkle:version>
      <sparkle:shortVersionString>$VERSION</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>$MIN_OS</sparkle:minimumSystemVersion>
      <sparkle:releaseNotesLink>https://github.com/rungmc357/speakeasy/releases/tag/v$VERSION</sparkle:releaseNotesLink>
      <pubDate>$(LC_ALL=C date -u "+%a, %d %b %Y %H:%M:%S +0000")</pubDate>
      <enclosure url="https://github.com/rungmc357/speakeasy/releases/download/v$VERSION/Speakeasy.dmg" $SIG_ATTRS type="application/octet-stream"/>
    </item>
  </channel>
</rss>
XML

if [[ "$PUBLISH" == "--publish" ]]; then
    # Also as a fixed name, so https://github.com/rungmc357/speakeasy/releases/latest/download/Speakeasy.dmg
    # always downloads the newest app directly (the website's Download button).
    # Only the fixed name is uploaded: the website, the in-app updater (which takes the first .dmg it
    # finds) and every download all get "Speakeasy.dmg", never a versioned file next to an old one.
    cp "$DMG" "$ROOT/dist/Speakeasy.dmg"
    (cd "$ROOT/dist" && shasum -a 256 Speakeasy.dmg > Speakeasy.dmg.sha256)
    gh release create "v$VERSION" "$ROOT/dist/Speakeasy.dmg" "$ROOT/dist/Speakeasy.dmg.sha256" "$ROOT/dist/appcast.xml" \
        --title "Speakeasy $VERSION" \
        --notes "$(latest_changes)

---
Signed and notarized macOS app (macOS 14+). New install: open the dmg and drag Speakeasy into Applications. Already on 0.2.10 or later: Check for Updates installs it in place. Everything that's changed: [CHANGELOG.md](https://github.com/rungmc357/speakeasy/blob/main/CHANGELOG.md)"
fi
echo "$DMG"
