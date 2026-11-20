"""Fixed identifiers. Tunable values live in defaults.toml."""

MIB = 1024 * 1024

APP_NAME = "Cannonball"
PACKAGE = "cannonball"
BUNDLE_ID = "com.thomer.cannonball"
DAEMON_LABEL = f"{BUNDLE_ID}.daemon"
DAEMON_PLIST = f"{DAEMON_LABEL}.plist"
DAEMON_ARG = "daemon"  # Cannonball.app argument that runs the daemon

CONFIG_PATH = f"~/.config/{PACKAGE}/config.toml"
LOG_FILE_AUTO = "auto"
MACOS_LOG_DIR = "~/Library/Logs"
LOG_NAME = f"{PACKAGE}.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
LOG_MAX_BYTES = 5_000_000
LOG_BACKUPS = 3

# Canon auto-send discovery (Bonjour).
SERVICE_TYPE = "_autosendimages._tcp"
SERVICE_DOMAIN = "local."
CAMERA_TXT_PAIRED_GUID = "tid.canon.com"  # in a camera's record: the computer it is paired with
CAMERA_TXT_MODEL = "mn.canon.com"
COMPUTER_TXT = {  # what Canon Image Transfer Utility 2 publishes, besides sid/mn below
    "srvver.canon.com": "1.0",
    "vever.canon.com": "0-1801-0101-01-00-00",
    "mf.canon.com": "Canon",
    "srv.canon.com": "0",
    "md.canon.com": "Canon Digital Camera",
}
COMPUTER_TXT_GUID = "sid.canon.com"
COMPUTER_TXT_NAME = "mn.canon.com"

# Canon Image Transfer Utility 2's stored pairing.
ITU2_PREFS = "~/Library/Preferences/com.canon.ImageTransferUtility2.plist"
ITU2_GUID_KEY = "ITU-initiatorGUID"

# Files in state_dir.
STATE_GUID = "guid"
STATE_HOSTS = "hosts.json"
STATE_STATUS = "status.json"
STATE_PAUSED = "paused"

# Status shown by the menu bar.
RECENT_FILES = 10
MENU_REFRESH_SECONDS = 1.0

# DNS-SD lookups (macOS): give up on a camera record that does not resolve.
DNSSD_RESOLVE_TIMEOUT = 5.0
DNSSD_LOOP_TICK = 1.0

# Waiting for a helper process (mDNS tools) to exit before killing it.
SUBPROCESS_STOP_TIMEOUT = 5.0

# macOS app bundle.
MIN_MACOS = "13.0"  # SMAppService
LOCAL_NETWORK_USAGE = f"{APP_NAME} receives photos from your camera over the local network."
LICENSE_NAME = "MIT License"
DAEMON_THROTTLE_SECONDS = 10  # launchd: least time between daemon restarts
