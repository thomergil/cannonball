<!-- AI-only. Terse facts. -->
# About panel and Sparkle updates — 2026-11-23 (contract v4, Cannonball 0.1.2)

Owner asked (2026-11-23) for an in-app version display and auto-update like sister apps Mutt Desktop and Open ScanSnap (`~/src/open-scansnap`; `updates.py` and the Sparkle build steps follow it). Owner approved both menu items.

## Decisions
- **About Cannonball** (no ellipsis, Apple HIG): standard macOS About panel. `menubar.about_options()`: ApplicationName, ApplicationVersion = `__version__`, Version = "" (else macOS appends "(CFBundleVersion)"), Credits = NSAttributedString link to `consts.HOMEPAGE`; macOS adds the icon. `MenuBar.about_` activates the app, then calls `orderFrontStandardAboutPanelWithOptions_`.
- **Check for Updates…** (title case like the other commands): Sparkle 2.10.1. `updates.updater()` → None outside the built app (`services.in_bundle()`) or when Sparkle.framework / `SPUStandardUpdaterController` fails to load (catches `ImportError`, `objc.error`); else starts `SPUStandardUpdaterController` with no delegates. Menu adds the item (target updater, action `checkForUpdates:`) only when the updater exists.
- Info.plist (`packaging/macos/Cannonball.spec`): `SUFeedURL` = `consts.UPDATE_FEED` (`HOMEPAGE/releases/latest/download/appcast.xml`), `SUPublicEDKey` = `consts.UPDATE_PUBLIC_KEY`, `SUEnableAutomaticChecks`, `SURequireSignedFeed`, `SUVerifyUpdateBeforeExtraction`.
- Key custody: EdDSA private key in the release signer's login Keychain, account `consts.UPDATE_KEY_ACCOUNT` ("cannonball"), made with `generate_keys --account cannonball`. Lost key → installed apps can never auto-update again; owner told to back it up (`generate_keys -x`).
- `scripts/build-macos.sh`:
  - Sparkle tarball pinned by version + SHA-256; cached in `build/cache`; downloads to `.part`, renamed only after the SHA-256 matches (an interrupted download never looks cached).
  - `RELEASE=1` fails early unless `generate_keys --account <account> -p` == `consts.UPDATE_PUBLIC_KEY`.
  - Removes Sparkle's XPCServices (app not sandboxed): both the top-level link and the `Versions/B` dir.
  - After `codesign --deep` signs the app (giving Sparkle the app's entitlements), re-signs Autoupdate, then Updater.app, then the framework, without entitlements (Sparkle docs advise against `--deep`); then reseals the app.
  - Copies Sparkle LICENSE to `Contents/Resources/Licenses/Sparkle.txt`.
  - `RELEASE=1` writes `dist/appcast.xml` via `generate_appcast --download-url-prefix consts.RELEASE_DOWNLOAD` (`HOMEPAGE/releases/download/v{version}/`); fails unless the appcast has the `sparkle-signatures` feed-signature block.
- Release assets per version: `Cannonball-<v>.dmg` + `.sha256` (appcast enclosure URL), `Cannonball.dmg` + `.sha256` (README latest link), `appcast.xml`. `SUFeedURL` points at the latest release's copy: if the latest release has no `appcast.xml` (e.g. PyPI-only), the feed returns 404 and Mac updates silently stop. Homebrew cask: `auto_updates true`.

## Dead ends (do not retry)
- `SPUStandardUserDriverDelegate` that activated the app for scheduled update alerts (so they would not hide behind windows in a Dock-less app): removed. Sparkle deliberately shows a background app's scheduled alert behind other windows, except near launch or when idle. The alert's Install button has keyboard focus, so activating the app brought it to the front, where a stray Return could install the update. Open ScanSnap also uses no delegate.

**Touches:** part menubar, part packaging, interface menubar→macOS, interface build→release, rule minimal-menu, rule pinned-wheels-only, `cannonball/updates.py`, `cannonball/menubar.py`, `cannonball/consts.py`, `packaging/macos/Cannonball.spec`, `scripts/build-macos.sh`
