#!/bin/zsh
# Builds Mike.app and installs it to ~/Applications.
#   ./app/build.sh            build + install
#   ./app/build.sh --no-install
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
MIKE_HOME="$(cd "$APP_DIR/.." && pwd)"
BUILD="$APP_DIR/build"
BUNDLE="$BUILD/Mike.app"

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"

echo "• compiling"
cp "$APP_DIR/Mike.swift" "$BUILD/main.swift"   # entry point must be main.swift with several files
swiftc -O -swift-version 5 -suppress-warnings -o "$BUNDLE/Contents/MacOS/Mike" "$BUILD/main.swift" "$APP_DIR/Control.swift" \
  -framework Cocoa -framework WebKit -framework Carbon -framework ScreenCaptureKit -framework AVFoundation -framework ServiceManagement

echo "• icon"
ICONSET="$BUILD/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
# App icon: app/icon-1024.png (regenerate with: swift app/make_icon.swift app/icon-1024.png).
if [[ -f "$APP_DIR/icon-1024.png" ]]; then
  cp "$APP_DIR/icon-1024.png" "$BUILD/icon-1024.png"
else
  swift "$APP_DIR/make_icon.swift" "$BUILD/icon-1024.png" >/dev/null
fi
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$BUILD/icon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$BUNDLE/Contents/Resources/AppIcon.icns"

echo "• Info.plist"
sed "s#__MIKE_HOME__#$MIKE_HOME#" "$APP_DIR/Info.plist" > "$BUNDLE/Contents/Info.plist"
plutil -lint "$BUNDLE/Contents/Info.plist" >/dev/null

echo "• signing (stable identity so macOS remembers permissions)"
IDENTITY=$(security find-identity -v -p codesigning | awk -F\" "/Apple Development/ {print \$2; exit}")
codesign --force --sign "${IDENTITY:--}" --identifier local.mike.assistant "$BUNDLE"

if [[ "${1:-}" != "--no-install" ]]; then
  mkdir -p "$HOME/Applications"
  rm -rf "$HOME/Applications/Mike.app"
  ditto "$BUNDLE" "$HOME/Applications/Mike.app"
  echo "✓ installed to ~/Applications/Mike.app"
else
  echo "✓ built $BUNDLE"
fi
