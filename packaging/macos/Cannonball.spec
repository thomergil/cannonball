# PyInstaller spec for Cannonball.app; built by scripts/build-macos.sh.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
sys.path.insert(0, ROOT)
from cannonball import __version__, consts  # noqa: E402

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, consts.PACKAGE, "defaults.toml"), consts.PACKAGE)],
    hiddenimports=["ServiceManagement", "PyObjCTools.AppHelper"],
    excludes=["tkinter", "unittest", "pydoc", "test"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=consts.APP_NAME, console=False, argv_emulation=False,
          icon=os.environ["ICON"])
coll = COLLECT(exe, a.binaries, a.datas, name=consts.APP_NAME)
app = BUNDLE(
    coll,
    name=f"{consts.APP_NAME}.app",
    bundle_identifier=consts.BUNDLE_ID,
    icon=os.environ["ICON"],  # drawn by icon.py
    version=__version__,
    info_plist={
        "CFBundleDevelopmentRegion": consts.DEVELOPMENT_REGION,
        "CFBundleName": consts.APP_NAME,
        "CFBundleDisplayName": consts.APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSUIElement": True,
        "LSMinimumSystemVersion": consts.MIN_MACOS,
        "NSLocalNetworkUsageDescription": consts.LOCAL_NETWORK_USAGE,
        "NSBonjourServices": [consts.SERVICE_TYPE],
        "NSHumanReadableCopyright": consts.LICENSE_NAME,
        # Sparkle (cannonball/updates.py); scripts/build-macos.sh adds the framework
        "SUFeedURL": consts.UPDATE_FEED,
        "SUPublicEDKey": consts.UPDATE_PUBLIC_KEY,
        "SUEnableAutomaticChecks": True,  # check without Sparkle's second-launch permission prompt
        "SURequireSignedFeed": True,  # the appcast itself must carry the update key's signature
        "SUVerifyUpdateBeforeExtraction": True,  # check the download's signature before opening it
    },
)

# A standard localization: macOS's Local Network privacy reads the app's name
# and usage description through it, and fails for a bundle without one.
strings = {
    "CFBundleName": consts.APP_NAME,
    "CFBundleDisplayName": consts.APP_NAME,
    "NSLocalNetworkUsageDescription": consts.LOCAL_NETWORK_USAGE,
}
lproj = os.path.join(DISTPATH, f"{consts.APP_NAME}.app", "Contents", "Resources", f"{consts.DEVELOPMENT_REGION}.lproj")
os.makedirs(lproj, exist_ok=True)
with open(os.path.join(lproj, "InfoPlist.strings"), "w", encoding="utf-8") as f:
    f.writelines(f'"{key}" = "{value}";\n' for key, value in strings.items())
