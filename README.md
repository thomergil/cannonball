<img src="https://raw.githubusercontent.com/thomergil/cannonball/main/packaging/macos/icon.png" width="96" alt="">

# Cannonball

Saves the photos a Canon EOS camera sends with **Auto send images to computer**.
A replacement for Canon Image Transfer Utility 2, for macOS and Linux. Tested
with an EOS 90D.

## Install

**macOS 13+, Apple Silicon:** download
[Cannonball.dmg](https://github.com/thomergil/cannonball/releases/latest/download/Cannonball.dmg)
([SHA-256](https://github.com/thomergil/cannonball/releases/latest/download/Cannonball.dmg.sha256)), drag
Cannonball to Applications, open it, and allow local network access. It lives
in the menu bar and opens at login. Or with Homebrew:

```sh
brew tap thomergil/tap
brew trust --cask thomergil/tap/cannonball   # Homebrew asks before loading a cask from a tap
brew install --cask thomergil/tap/cannonball
```

**Linux** (Python 3.11+, systemd; uses avahi-daemon if it is running, otherwise python-zeroconf):

```sh
uv tool install cannonball-eos   # or: pipx install cannonball-eos
cannonball --install-service     # runs it now and at login
```

## Pair the camera

With Cannonball running, on the camera: **Wireless settings > Wi-Fi function >
Auto send images to computer > Auto send: Enable**, then pick your network and
this computer. Switch the camera off and on; from then on it sends new photos
each time it is switched on. A pairing made with Canon's utility on this Mac
works as is.

## Configure

Optional, in `~/.config/cannonball/config.toml`:

```toml
hosts = ["192.168.1.50"]       # the camera's address, if fixed; Cannonball then finds it faster
networks = ["192.168.1.0/24"]  # where Cannonball works; default: private and link-local ranges

[download]
destination = "~/Pictures"
subfolder = "{date:%Y_%m_%d}"  # by capture date; "" for none
```

Every setting is in [`cannonball/defaults.toml`](https://github.com/thomergil/cannonball/blob/main/cannonball/defaults.toml).
Restart Cannonball after a change (Linux: `systemctl --user restart cannonball`). Logs: `~/Library/Logs/cannonball.log` (macOS),
`~/.local/state/cannonball/cannonball.log` (Linux).

## Notes

- A photo is confirmed to the camera only once it is on disk; an interrupted one
  is sent again, from the start.
- The camera's Wi-Fi is fickle: a transfer can take two or three tries. If it
  fails, switch the camera off, wait a few seconds, switch it on, and wait 30
  seconds; the first photo can take that long to start.
- While photos are coming in, the menu bar camera shimmers and the red camera
  icon on the camera flickers.
- The camera decides which photos to send (Image sending options).
- Any device on `networks` can pose as the camera. Cannonball writes only plain
  files inside the download folder, and on macOS opens no listening sockets.

## Development

```sh
python3 -m venv env
env/bin/pip install --require-hashes --only-binary :all: -r requirements.txt -r requirements-dev.txt
env/bin/python -m cannonball -v   # run in the foreground
env/bin/python -m pytest          # tests, against a simulated camera
scripts/build-macos.sh            # dist/Cannonball-<version>.dmg
```

## Sources

- Protocol: Canon's auto-send session as documented from packet captures by
  [epsimatic/canon-wifi-downloader](https://github.com/epsimatic/canon-wifi-downloader/blob/85b60cd468af20db3940dec9d1ba7d2a7a229afe/PROTOCOL.md).
  The tests check that Cannonball's own client sends exactly the same bytes as
  that project's, which they download at a pinned commit and verify by SHA-256.
- PTP/IP: CIPA DC-X005; Canon opcodes and session ID from
  [libgphoto2](https://github.com/gphoto/libgphoto2) and its
  [issue #333](https://github.com/gphoto/libgphoto2/issues/333).
- Libraries: [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) (LGPL-2.1),
  [ifaddr](https://github.com/ifaddr/ifaddr) (MIT), [PyObjC](https://github.com/ronaldoussoren/pyobjc) (MIT).
  The app is built with [PyInstaller](https://github.com/pyinstaller/pyinstaller) on
  [python-build-standalone](https://github.com/astral-sh/python-build-standalone).
- Supply chain: the Mac app and the tests use dependency wheels pinned by
  SHA-256 (matching PyPI), and the PyPI package pins the same versions. The
  bundled Python is checksum-verified by [uv](https://github.com/astral-sh/uv),
  and CI actions are pinned by commit.

## License

MIT. Not affiliated with Canon. Canon and EOS are trademarks of Canon Inc.
