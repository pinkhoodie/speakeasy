#!/usr/bin/env bash
# Sign an embedded Sparkle.framework inside-out (its XPC services, updater app and Autoupdate first).
#   scripts/sign-sparkle.sh <Sparkle.framework> <identity> [extra codesign args...]
# Release builds pass "--options runtime --timestamp" so notarization accepts every nested binary.
set -euo pipefail
FW="$1"; ID="$2"; shift 2
V="$FW/Versions/B"
for item in "$V/XPCServices/Installer.xpc" "$V/XPCServices/Downloader.xpc" "$V/Autoupdate" "$V/Updater.app"; do
    [[ -e "$item" ]] || continue
    if [[ "$item" == *Downloader.xpc ]]; then
        # The downloader keeps Sparkle's own entitlements (network client).
        codesign --force --sign "$ID" "$@" --preserve-metadata=entitlements "$item"
    else
        codesign --force --sign "$ID" "$@" "$item"
    fi
done
codesign --force --sign "$ID" "$@" "$FW"
