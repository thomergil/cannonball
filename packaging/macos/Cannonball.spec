# PyInstaller spec for Cannonball.app; built by scripts/build-macos.sh.
import os
import plistlib
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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=consts.APP_NAME, console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name=consts.APP_NAME)
app = BUNDLE(
    coll,
    name=f"{consts.APP_NAME}.app",
    bundle_identifier=consts.BUNDLE_ID,
    version=__version__,
    info_plist={
        "CFBundleName": consts.APP_NAME,
        "CFBundleDisplayName": consts.APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "LSUIElement": True,
        "LSMinimumSystemVersion": consts.MIN_MACOS,
        "NSLocalNetworkUsageDescription": consts.LOCAL_NETWORK_USAGE,
        "NSBonjourServices": [consts.SERVICE_TYPE],
        "NSHumanReadableCopyright": consts.LICENSE_NAME,
    },
)

# The daemon, registered with SMAppService by the app (services.py).
program = f"Contents/MacOS/{consts.APP_NAME}"
agents = os.path.join(DISTPATH, f"{consts.APP_NAME}.app", "Contents", "Library", "LaunchAgents")
os.makedirs(agents, exist_ok=True)
with open(os.path.join(agents, consts.DAEMON_PLIST), "wb") as f:
    plistlib.dump(
        {
            "Label": consts.DAEMON_LABEL,
            "BundleProgram": program,
            "ProgramArguments": [program, consts.DAEMON_ARG],
            "AssociatedBundleIdentifiers": [consts.BUNDLE_ID],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": consts.DAEMON_THROTTLE_SECONDS,
            "ProcessType": "Interactive",
        },
        f,
    )
