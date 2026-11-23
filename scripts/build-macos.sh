#!/bin/sh
# Build dist/<App>-<version>.dmg for this Mac's architecture.
#
#   SIGNING_IDENTITY  codesign identity; default: the login Keychain's
#                     "Developer ID Application" identity, else ad-hoc
#   NOTARY_KEY_PATH   App Store Connect API key (.p8); with NOTARY_KEY_ID and
#                     NOTARY_ISSUER_ID, the image is notarized and stapled
#   RELEASE=1         refuse to finish without a Developer ID identity, notarization
#                     and the Sparkle update key; also writes dist/appcast.xml, the
#                     update feed, signed with that key
#   UV                uv binary (default: uv)
#
# Python comes from python-build-standalone via uv, which checks it against
# its published SHA-256. Dependencies are wheels pinned by hash in
# requirements*.txt; Sparkle is pinned by SHA-256 below. Nothing is built
# from source.
set -eu
cd "$(dirname "$0")/.."

PYTHON_VERSION=3.14.10
UV=${UV:-uv}
B=build/macos
CACHE=build/cache
# Sparkle, for software updates (cannonball/updates.py): release and its published SHA-256
SPARKLE_VERSION=2.10.1
SPARKLE_SHA256=1a856d2a9edb8e628de88121d7be04cf0e2167f8641650b78fca00b14244cf27

rm -rf "$B"
mkdir -p "$B" dist "$CACHE"
SPARKLE_ARCHIVE=$CACHE/Sparkle-$SPARKLE_VERSION.tar.xz
sparkle_ok() { echo "$SPARKLE_SHA256  $1" | shasum -a 256 -c - >/dev/null 2>&1; }
if ! sparkle_ok "$SPARKLE_ARCHIVE"; then
  # a download goes to .part first, so an interrupted one never looks cached
  curl -fsSL -o "$SPARKLE_ARCHIVE.part" \
    "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"
  sparkle_ok "$SPARKLE_ARCHIVE.part" || { rm -f "$SPARKLE_ARCHIVE.part"; echo "Sparkle download does not match SPARKLE_SHA256." >&2; exit 1; }
  mv "$SPARKLE_ARCHIVE.part" "$SPARKLE_ARCHIVE"
fi
SPARKLE=$B/sparkle
mkdir -p "$SPARKLE"
tar -xJf "$SPARKLE_ARCHIVE" -C "$SPARKLE"
"$UV" python install --install-dir "$B/python" "$PYTHON_VERSION"
PY=$(find "$B/python" -path "*/bin/python${PYTHON_VERSION%.*}" -type f | head -1)
"$PY" -m venv "$B/venv"
VPY=$B/venv/bin/python
"$VPY" -m pip install -q --require-hashes --only-binary :all: -r requirements.txt -r requirements-dev.txt -r requirements-dmg.txt

APP_NAME=$("$VPY" -c 'from cannonball import consts; print(consts.APP_NAME)')
VERSION=$("$VPY" -c 'from cannonball import __version__; print(__version__)')
RELEASE_DOWNLOAD=$("$VPY" -c 'from cannonball import __version__, consts; print(consts.RELEASE_DOWNLOAD.format(version=__version__))')
UPDATE_KEY_ACCOUNT=$("$VPY" -c 'from cannonball import consts; print(consts.UPDATE_KEY_ACCOUNT)')
UPDATE_PUBLIC_KEY=$("$VPY" -c 'from cannonball import consts; print(consts.UPDATE_PUBLIC_KEY)')
APP=$B/dist/$APP_NAME.app
DMG=dist/$APP_NAME-$VERSION.dmg
rm -f "$DMG" "$DMG.sha256"

if [ -z "${SIGNING_IDENTITY:-}" ]; then
  SIGNING_IDENTITY=$(security find-identity -v -p codesigning \
    | awk -F'"' '/Developer ID Application/ { print $2; exit }')
fi
SIGNING_IDENTITY=${SIGNING_IDENTITY:--}
if [ "${RELEASE:-}" = 1 ]; then
  if [ "$SIGNING_IDENTITY" = - ] || [ -z "${NOTARY_KEY_PATH:-}" ]; then
    echo "RELEASE=1 needs a Developer ID Application identity and NOTARY_KEY_PATH, NOTARY_KEY_ID, NOTARY_ISSUER_ID." >&2
    exit 1
  fi
  # Checked before the slow steps: installed apps reject updates signed with any other key
  KEYCHAIN_KEY=$("$SPARKLE/bin/generate_keys" --account "$UPDATE_KEY_ACCOUNT" -p 2>/dev/null || true)
  if [ "$KEYCHAIN_KEY" != "$UPDATE_PUBLIC_KEY" ]; then
    echo "RELEASE=1 needs the Sparkle update key for account $UPDATE_KEY_ACCOUNT in the login Keychain, matching consts.UPDATE_PUBLIC_KEY." >&2
    exit 1
  fi
fi

ICON=$PWD/$B/$APP_NAME.icns
"$VPY" packaging/macos/icon.py "$ICON"
ICON=$ICON "$B/venv/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$B/dist" --workpath "$B/work" packaging/macos/Cannonball.spec
FRAMEWORK=$APP/Contents/Frameworks/Sparkle.framework
ditto "$SPARKLE/Sparkle.framework" "$FRAMEWORK"
# Sparkle's XPC services serve sandboxed apps only; Cannonball is not sandboxed
rm -rf "$FRAMEWORK/XPCServices" "$FRAMEWORK/Versions/B/XPCServices"
mkdir -p "$APP/Contents/Resources/Licenses"
cp "$SPARKLE/LICENSE" "$APP/Contents/Resources/Licenses/Sparkle.txt"  # Sparkle's MIT license requires its notice in every copy

if [ "$SIGNING_IDENTITY" = - ]; then
  echo "No Developer ID identity: signing ad-hoc." >&2
  codesign --force --deep --sign - "$APP"
else
  codesign --force --deep --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist --sign "$SIGNING_IDENTITY" "$APP"
  # The deep pass gave Sparkle the app's entitlements. Sparkle's documentation
  # signs its parts from the inside out, without them; the app is then sealed
  # again over the re-signed framework.
  for part in Versions/B/Autoupdate Versions/B/Updater.app .; do
    codesign --force --options runtime --timestamp --sign "$SIGNING_IDENTITY" "$FRAMEWORK/$part"
  done
  codesign --force --options runtime --timestamp \
    --entitlements packaging/macos/entitlements.plist --sign "$SIGNING_IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"

# The image opens on the app, an arrow and Applications (packaging/macos/dmg-settings.py)
"$VPY" packaging/macos/dmg_background.py "$B/dmg-background"
"$B/venv/bin/dmgbuild" -s packaging/macos/dmg-settings.py -D "settings_dir=$PWD/packaging/macos" \
  -D "app=$APP" -D "background=$B/dmg-background/background.png" "$APP_NAME" "$DMG"

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
if [ "${RELEASE:-}" = 1 ]; then
  # Write the update feed for this release, signed with the update key. Upload
  # dist/appcast.xml with the release: SUFeedURL reads the newest release's copy.
  APPCAST_DIR=$B/appcast
  mkdir -p "$APPCAST_DIR"
  cp "$DMG" "$APPCAST_DIR/"
  "$SPARKLE/bin/generate_appcast" --account "$UPDATE_KEY_ACCOUNT" \
    --download-url-prefix "$RELEASE_DOWNLOAD" "$APPCAST_DIR"
  # the app requires a signed feed (SURequireSignedFeed); an unsigned one would stop all updates
  grep -q 'sparkle-signatures' "$APPCAST_DIR/appcast.xml" || {
    echo "generate_appcast wrote no feed signature." >&2
    exit 1
  }
  cp "$APPCAST_DIR/appcast.xml" dist/appcast.xml
fi

# Leave only the image: macOS mixes up apps with several copies installed
# (TN3179, FB15568200), and LaunchServices registers these build copies.
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
"$LSREGISTER" -u "$APP" 2>/dev/null || true
rm -rf "$B/dist"
echo "$DMG"
