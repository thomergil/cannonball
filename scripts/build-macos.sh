#!/bin/sh
# Build dist/<App>-<version>.dmg for this Mac's architecture.
#
#   SIGNING_IDENTITY  codesign identity; default: the login Keychain's
#                     "Developer ID Application" identity, else ad-hoc
#   NOTARY_KEY_PATH   App Store Connect API key (.p8); with NOTARY_KEY_ID and
#                     NOTARY_ISSUER_ID, the image is notarized and stapled
#   UV                uv binary (default: uv)
#
# Python comes from python-build-standalone via uv, which checks it against
# its published SHA-256. Dependencies are wheels pinned by hash in
# requirements*.txt; nothing is built from source.
set -eu
cd "$(dirname "$0")/.."

PYTHON_VERSION=3.14
UV=${UV:-uv}
B=build/macos

rm -rf "$B"
mkdir -p "$B" dist
"$UV" python install --install-dir "$B/python" "$PYTHON_VERSION"
PY=$(find "$B/python" -path "*/bin/python$PYTHON_VERSION" -type f | head -1)
"$PY" -m venv "$B/venv"
VPY=$B/venv/bin/python
"$VPY" -m pip install -q --require-hashes --only-binary :all: -r requirements.txt -r requirements-dev.txt

APP_NAME=$("$VPY" -c 'from cannonball import consts; print(consts.APP_NAME)')
VERSION=$("$VPY" -c 'from cannonball import __version__; print(__version__)')
APP=$B/dist/$APP_NAME.app
DMG=dist/$APP_NAME-$VERSION.dmg
rm -f "$DMG" "$DMG.sha256"

if [ -z "${SIGNING_IDENTITY:-}" ]; then
  SIGNING_IDENTITY=$(security find-identity -v -p codesigning \
    | awk -F'"' '/Developer ID Application/ { print $2; exit }')
fi
SIGNING_IDENTITY=${SIGNING_IDENTITY:--}

"$B/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$B/dist" --workpath "$B/work" packaging/macos/Cannonball.spec

if [ "$SIGNING_IDENTITY" = - ]; then
  echo "No Developer ID identity: signing ad-hoc." >&2
  codesign --force --deep --sign - "$APP"
else
  codesign --force --deep --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist --sign "$SIGNING_IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"

mkdir -p "$B/dmg"
cp -R "$APP" "$B/dmg/"
ln -s /Applications "$B/dmg/Applications"
hdiutil create -quiet -volname "$APP_NAME" -srcfolder "$B/dmg" -format UDZO "$DMG"

if [ "$SIGNING_IDENTITY" != - ]; then
  codesign --force --timestamp --sign "$SIGNING_IDENTITY" "$DMG"
  if [ -n "${NOTARY_KEY_PATH:-}" ]; then
    xcrun notarytool submit "$DMG" --key "$NOTARY_KEY_PATH" \
      --key-id "${NOTARY_KEY_ID:?}" --issuer "${NOTARY_ISSUER_ID:?}" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
  fi
fi
(cd dist && shasum -a 256 "$(basename "$DMG")" > "$(basename "$DMG").sha256")
echo "$DMG"
