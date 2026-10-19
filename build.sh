#!/bin/zsh
# Builds build/LeetTrace.app (no Xcode project needed).
set -euo pipefail
cd "$(dirname "$0")"

APP=build/LeetTrace.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

swiftc -swift-version 5 -parse-as-library -O \
  -target "$(uname -m)-apple-macos13.0" \
  Sources/LeetTraceApp.swift \
  -o "$APP/Contents/MacOS/LeetTrace"

cp Info.plist "$APP/Contents/"
cp -R web "$APP/Contents/Resources/web"
cp tracer/tracer.py "$APP/Contents/Resources/"
[[ -f AppIcon.icns ]] && cp AppIcon.icns "$APP/Contents/Resources/"

codesign --force --deep -s - "$APP"
echo "Built $APP"
