<!-- AI-only. Terse facts. -->
# Discovery and announcement (Bonjour) — 2026-11-19..20

## Decisions
- Computer publishes `_autosendimages._tcp` with ITU2's TXT (`sid.canon.com` = pairing GUID upper-case, `mn.canon.com` = name, plus `consts.COMPUTER_TXT`). Port in record = `announce_port` (15741); nothing listens on it; Cannonball always connects out.
- Verified (90D): camera opens PTP/IP only after hearing this computer's record announced while the camera is awake, and turns Wi-Fi OFF within seconds otherwise. Hence re-announce when a known host turns reachable, and every `announce_interval` while idle (rule announce-on-wake).
- Camera's own record (`tid.canon.com` = pairing GUID) is not always published after it has found the computer. So camera addresses come from three sources: config `hosts`, discovery, `state_dir/hosts.json` (remembered). Detection speed target: seconds, not 30 s (owner) — ping every `poll_interval` (1 s); a discovery result wakes the supervisor loop.
- GUID reuse: if Canon ITU2 paired on this Mac, use its `ITU-initiatorGUID` from its plist so no re-pairing is needed.
- Backends: macOS → mDNSResponder via ctypes `dnssd.py` (opens no sockets of its own); Linux → Avahi CLI tools when avahi-daemon runs; else python-zeroconf bound to `networks` interfaces.
- Default scope: private ranges only; configurable `networks` (owner: announce, browse, and accept cameras only on well-known local networks by default).

## Dead ends (do not retry)
- python-zeroconf's own responder on macOS: camera ignores records answered by a second responder; must go through mDNSResponder.

**Touches:** part discovery, part dnssd, part supervisor, interface supervisor→discovery, interface discovery(Mac)→dnssd, rule network-scope, rule mac-mdns-via-mdnsresponder, rule announce-on-wake, `cannonball/discovery.py`, `cannonball/dnssd.py`, `cannonball/__main__.py`
