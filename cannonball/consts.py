"""Fixed values. Settings a user may change live in defaults.toml."""

MIB = 1024 * 1024
PARTIAL_NAME = ".{name}.cannonball"  # an image while it downloads, next to where it goes

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
STATE_ALERTED = "local-network-alert-shown"  # present once the Local Network dialog was shown; removed when a session connects
STATE_HOSTS = "hosts.json"
STATE_PARTIAL = "partial"  # path of the temp file being written, so the next session can remove it if this one is killed

# Status shown by the menu bar.
MENU_REFRESH_SECONDS = 0.4

# Discovery: give up on a camera record that does not resolve (seconds).
RESOLVE_TIMEOUT = 5.0
DNSSD_LOOP_TICK = 1.0  # seconds; longest select() wait on the DNS-SD thread
DNSSD_MAX_RESOLVES = 16  # records resolved at once; further records are skipped, so a flood cannot exhaust file descriptors
REMEMBERED_HOSTS = 4  # camera addresses found on the network and kept across runs

# Waiting for a helper process (mDNS tools) to exit before killing it.
SUBPROCESS_STOP_TIMEOUT = 5.0  # seconds
NO_SPACE_RETRY = 60.0  # seconds before trying a camera again after the disk was full
DISCOVERY_RETRY = 10.0  # seconds between attempts to restart failed announcing/browsing

# macOS app bundle.
MIN_MACOS = "13.0"  # SMAppService login item
DEVELOPMENT_REGION = "en"
LOCAL_NETWORK_USAGE = f"{APP_NAME} receives photos from your camera over the local network."
LOCAL_NETWORK_HINT = f"Allow {APP_NAME} in System Settings > Privacy & Security > Local Network"
LOGIN_ITEM_HINT = f"Allow {APP_NAME} in System Settings > General > Login Items"
LOGIN_ITEM_FAILED = f"Could not open {APP_NAME} at login: {{reason}}"
LICENSE_NAME = "MIT License"
HOMEPAGE = "https://github.com/thomergil/cannonball"
# Sparkle updates. The feed is the appcast.xml of the newest GitHub release; its
# entries download from the release tagged v<version>. The update key's private
# half is in the release signer's login Keychain under UPDATE_KEY_ACCOUNT
# (created with Sparkle's generate_keys --account <UPDATE_KEY_ACCOUNT>).
UPDATE_FEED = f"{HOMEPAGE}/releases/latest/download/appcast.xml"
RELEASE_DOWNLOAD = HOMEPAGE + "/releases/download/v{version}/"
UPDATE_PUBLIC_KEY = "ihsIfgTgFbeTwQL8C+gc/akxd4cIjfQaYrvviJEGI0k="
UPDATE_KEY_ACCOUNT = PACKAGE
# macOS 26 has no link to the Local Network page itself, only to Privacy & Security.
LOCAL_NETWORK_SETTINGS = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"
PROBE_PORT = 9  # discard (RFC 863): localnet connects a UDP socket to it and sends nothing
LOCAL_NETWORK_GRACE = 30.0  # seconds the user has to answer the macOS Local Network prompt before Cannonball explains how to allow access
LOCAL_NETWORK_RECHECK = 5.0  # seconds between checks while access is missing
