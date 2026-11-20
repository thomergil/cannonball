"""Fixed identifiers. Tunable values live in defaults.toml."""

MIB = 1024 * 1024

APP_NAME = "Cannonball"
PACKAGE = "cannonball"
BUNDLE_ID = "com.thomergil.cannonball"

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
STATE_ALERTED = "local-network-alert-shown"  # the access dialog is shown once, ever
STATE_HOSTS = "hosts.json"

# Status shown by the menu bar.
MENU_REFRESH_SECONDS = 0.4  # also one step of the Wi-Fi badge animation

# DNS-SD lookups (macOS): give up on a camera record that does not resolve.
DNSSD_RESOLVE_TIMEOUT = 5.0
DNSSD_LOOP_TICK = 1.0

# Waiting for a helper process (mDNS tools) to exit before killing it.
SUBPROCESS_STOP_TIMEOUT = 5.0

# macOS app bundle.
MIN_MACOS = "13.0"  # SMAppService login item
DEVELOPMENT_REGION = "en"
LOCAL_NETWORK_USAGE = f"{APP_NAME} receives photos from your camera over the local network."
LOCAL_NETWORK_HINT = f"Allow {APP_NAME} in System Settings > Privacy & Security > Local Network"
LICENSE_NAME = "MIT License"
# macOS 26 has no link to the Local Network page itself, only to Privacy & Security.
LOCAL_NETWORK_SETTINGS = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
PROBE_PORT = 9  # connected to, never sent to
LOCAL_NETWORK_GRACE = 30.0  # seconds to answer macOS's own question before we complain
LOCAL_NETWORK_RECHECK = 5.0  # seconds between checks while access is missing
