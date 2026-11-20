# Cannonball

Wi-Fi image transfer utility for Canon EOS cameras. It receives the photos a
camera sends with **Auto send images to computer** and saves them to a folder.
It replaces Canon Image Transfer Utility 2 for this one job. macOS and Linux.

Tested with an EOS 90D. Other EOS models with auto-send may work; reports welcome.

## Install

**macOS 13 or later:** download the `.dmg` from
[Releases](https://github.com/thomergil/cannonball/releases), drag Cannonball to
Applications and open it. A camera icon appears in the menu bar and Cannonball
starts at login. Allow local network access when macOS asks.

**Linux:**

```sh
git clone https://github.com/thomergil/cannonball ~/cannonball
cd ~/cannonball
python3 -m venv env
env/bin/pip install --require-hashes --only-binary :all: -r requirements.txt
mkdir -p ~/.config/systemd/user
cp packaging/linux/cannonball.service ~/.config/systemd/user/
systemctl --user enable --now cannonball
```

Needs Python 3.11+. Uses Avahi when it is running.

## Pair the camera

With Cannonball running, on the camera: **Wireless settings > Wi-Fi function >
Auto send images to computer > Auto send: Enable**, choose your Wi-Fi network and
then this computer. After pairing, switch the camera off and on. From then on it
sends new images whenever it is switched on near your network.

If the camera was paired with Canon's utility on this Mac, Cannonball uses that
pairing.

## Configure

Optional. `~/.config/cannonball/config.toml`:

```toml
hosts = ["192.168.1.50"]       # camera IP; optional, but a fixed address here makes detection faster
networks = ["192.168.1.0/24"]  # where Cannonball works; default: the private ranges

[download]
destination = "~/Pictures"
subfolder = "{date:%Y_%m_%d}"  # by capture date; "" for none
```

All settings and defaults: [`cannonball/defaults.toml`](cannonball/defaults.toml). Restart Cannonball
after editing (macOS: menu > Stop Cannonball, then open it again; Linux:
`systemctl --user restart cannonball`).

Logs: `~/Library/Logs/cannonball.log` (macOS), `~/.local/state/cannonball/cannonball.log` (Linux).

## Notes

- Cannonball only works on the networks in `networks`. On macOS it announces
  and searches through the system's Bonjour service and opens no listening
  sockets.
- An image is confirmed to the camera only after it is saved. If a transfer
  is interrupted, the camera sends the image again, from the start, next time.
- If the camera's Wi-Fi indicator says OFF, switch the camera off and on.
- Which images are sent is set on the camera (Image sending options).

## Development

```sh
python3 -m venv env
env/bin/pip install --require-hashes --only-binary :all: -r requirements.txt -r requirements-dev.txt
env/bin/python -m cannonball -v     # run in the foreground
env/bin/python -m pytest            # tests, against a simulated camera
scripts/build-macos.sh              # dist/Cannonball-<version>.dmg
```

## Sources

- Protocol: Canon's auto-send session was documented from packet captures by
  [epsimatic/canon-wifi-downloader](https://github.com/epsimatic/canon-wifi-downloader)
  ([PROTOCOL.md](https://github.com/epsimatic/canon-wifi-downloader/blob/85b60cd468af20db3940dec9d1ba7d2a7a229afe/PROTOCOL.md)).
  Cannonball's PTP/IP client is its own. The tests check that it sends exactly
  the same bytes as that project's client, which is downloaded at a pinned
  commit and verified by SHA-256; none of its code is included.
- PTP/IP framing: CIPA DC-X005. Canon opcode names and the session ID 0x41:
  [libgphoto2](https://github.com/gphoto/libgphoto2) and its
  [issue #333](https://github.com/gphoto/libgphoto2/issues/333).
- Libraries: [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) (LGPL-2.1),
  [ifaddr](https://github.com/ifaddr/ifaddr) (MIT),
  [PyObjC](https://github.com/ronaldoussoren/pyobjc) (MIT, macOS).
  The macOS app is built with [PyInstaller](https://github.com/pyinstaller/pyinstaller)
  on [python-build-standalone](https://github.com/astral-sh/python-build-standalone).

Supply chain: every dependency is pinned by SHA-256 in `requirements*.txt`
(matching PyPI's published hashes) and installed as a wheel only; nothing is
built from source. The bundled Python is checksum-verified by
[uv](https://github.com/astral-sh/uv). CI actions are pinned by commit.

## License

MIT. Not affiliated with Canon. Canon and EOS are trademarks of Canon Inc.
