<!-- AI-only. Terse facts. -->
# Architecture Contract — v4
> Binding. Enforce holds code to Parts, Interfaces, Rules; changes bump version
> and add a History entry under .architecture/history/. Intent is nonbinding.

Product: receive Canon EOS "Auto send images to computer" Wi-Fi transfers, save to a folder. macOS menu bar app + Linux systemd user service. MIT. Tested on EOS 90D only.
Owner priorities, in order: robustness with default settings and no user setup > radical simplicity > speed of detection (seconds) > transfer optimizations. Claims stay modest; sources credited (README "Sources").

## Parts
- **config** (`cannonball/config.py`, `defaults.toml`, `consts.py`) — load `defaults.toml` overridden key by key by `~/.config/cannonball/config.toml`; derive GUID, networks, paths; fixed identifiers in `consts.py`.
- **supervisor** (`cannonball/__main__.py`) — CLI entry; announce, watch known camera hosts by ping, run one worker process at a time, kill on stall, retry; never speaks PTP.
- **worker** (`cannonball/worker.py`) — one camera session in a spawned child process: ITU2 flow, download, save, acknowledge.
- **canon** (`cannonball/canon.py`) — Canon auto-send op codes, op sequences, GetEvent/ObjectInfo parsers; pure, no I/O.
- **ptpip** (`cannonball/ptpip.py`) — minimal PTP/IP initiator (CIPA DC-X005), command + event connection; no Canon knowledge.
- **discovery** (`cannonball/discovery.py`) — announce this computer's `_autosendimages._tcp` record, report paired cameras; backends Mac (mDNSResponder), Avahi CLI, python-zeroconf.
- **dnssd** (`cannonball/dnssd.py`) — ctypes binding to mDNSResponder `dns_sd.h`; one thread owns all DNS-SD refs.
- **status** (`cannonball/status.py`) — in-memory supervisor state for the menu bar; typed `Progress` (worker → supervisor) and frozen `Snapshot`.
- **menubar** (`cannonball/menubar.py`, `app.py`, `localnet.py`, `services.py`, `updates.py`, `drawing.py`) — macOS app: PyObjC status item, supervisor on a thread, Local Network permission prompt/alert, SMAppService login item, standard About panel, Sparkle updater (`updates.py`); `drawing` (circle, stroke, Wi-Fi fan) is also used by `packaging/macos/icon.py`; fixed-width status item; camera icon shimmers only while photos arrive (RECEIVING), holds a still frame while the camera connects (CAMERA); the frame timer runs only while the shimmer is shown.
- **packaging** (`packaging/`, `scripts/build-macos.sh`) — PyInstaller `.app` with embedded, re-signed Sparkle.framework → signed/notarized `.dmg`, plus a signed appcast (`dist/appcast.xml`); Linux systemd unit.
- **tests** (`tests/`) — fake 90D (`fakecam.py`), golden wire recordings (`golden/`), differential test vs pinned GPL reference client.

## Interfaces

### supervisor → config · v2 · function
`config.load(path: Path | None) -> Config`. Merges `defaults.toml` with user file; `defaults.toml` is the schema: user keys must exist there and match the default's type (int widens to float; bool is not int). Floats finite. Ranges: `config.RANGES`, timing ≥ 0 (`POSITIVE_TIMINGS` > 0). `Download.folder(when)` owns the folder rule (pattern formats to a relative path without `..`; symlinks allowed); `destination` absolute; `name` ≤ 63 bytes; `event_poll`, `connect_timeout`, `read_timeout` < `stall_timeout`; load checks it, worker uses it. Errors: `ConfigError(SystemExit)` one-line message `<path>: <problem>` (unknown key, bad type or range, TOML syntax, unreadable file; `supervisor()` adds an unusable `log_file`).
`Config.networks: Networks`; `addr in networks` (IPv4 str); `networks.interfaces() -> {if_index: ipv4}`.
GUID precedence: config `guid` > ITU2 plist `ITU-initiatorGUID` > `state_dir/guid` (generated once).
App: `ConfigError` → line in `config.macos_log()` + one alert naming the problem, then quit (owner: a modal when Cannonball cannot work and the user can fix it).

### supervisor → worker · v2 · process (multiprocessing spawn)
`ctx.Process(target=worker.main, args=(cfg, host, heartbeat: mp.Value('d'), log_queue: mp.Queue, events: mp.Queue))`.
- Exit codes: `OK=0` session completed → next try after `timing.resync_after`; `NO_CAMERA=3` no PTP/IP session within `timing.connect_window`; `FAILED=4` session broke → retry after `timing.retry_delay`; `NO_SPACE=5` disk too full → retry after `consts.NO_SPACE_RETRY`; killed/other → retry after `timing.retry_delay`.
- `heartbeat.value = time.time()` on each connect attempt, chunk, GetEvent poll. Supervisor kills worker when `heartbeat.value` is older than `timing.stall_timeout`.
- `events`: `status.Progress(event, file, count, message)`: `CONNECTED`; `RECEIVING{file}`; `SAVED`; `FINISHED{count}`; `ERROR{message}`; `REFUSED` (camera pings, macOS refuses connections). Supervisor tolerates half-written events from a killed worker.
- Logs: worker root logger → `QueueHandler(log_queue)`; supervisor `QueueListener` writes them.

### worker → ptpip · v2 · function
`Client(host, guid: UUID, name, *, port, connect_timeout, op_timeout)`; `.connect()`; `.op(code, *params: int, timeout: float | None = None) -> (rc: int, params: list[int], data: bytes)`; `.close()`; `.camera_name`.
- `op` never raises on non-OK `rc`; raises `ProtocolError` on a PTP/IP protocol failure (Init_Fail, short/bad framing, Cancel, tid mismatch, second Start_Data, data phase or Data payload > `MAX_DATA` (16 MiB; packet may add `DATA_HEADER`), control packet > `MAX_CONTROL`, > `MAX_PARAMS`) and `OSError` on transport failure (incl. `TimeoutError` after `timeout` (default `op_timeout`) without progress: only Start_Data and non-empty Data reset it). Image reads use `timing.read_timeout`: the 90D sends nothing for over 10 s on a session's first read, and a session that gives up loses the camera's Wi-Fi.
- Event connection: kept open, Probe_Request answered on both connections, Data packets limited to `MAX_CONTROL`; events unused (Canon reports via GetEvent). `camera_name` passes `printable()`.
- Client override: `worker.Session.client_class` (tests set it to the reference client).

### worker → canon · v2 · function
Op constants and sequences `SETUP`, `BEFORE_FIRST_READ`, `FINISH`; `events(data) -> Iterator[(kind, body)]`; `file_name(handle, *candidates) -> str` (first candidate whose last component matches `FILE_NAME` with an extension in `MEDIA_EXTENSIONS`, else `IMG_<handle>`; never a leading dot/dash or path); `objects(data) -> [ObjectRecord(handle,size,path,format)]` (0xC1AB only); `object_info(data) -> ObjectInfo | None`; `capture_time(str) -> datetime | None`. Parsers never raise on malformed input.

Camera session (wire contract, pinned by `tests/golden/`):
```
connect (Init_Command GUID+name, Init_Event) within connect_window
OpenSession 0x1002[0x41] + SETUP, all sent before checking rc; any non-OK → FAILED
loop: per 0xC1AB record: 0x9170 info → (first read only) BEFORE_FIRST_READ
      → 0x9174[h, off, chunk, 0, 0] from off=0, each followed by 0x9102, 0x91E9 progress
      → flush .<name>.cannonball, rename, flush folder → 0x91F0[h] → 0x9177[h, 0]
      no record: sleep event_poll; stop after idle_timeout; 0x9116 GetEvent
FINISH (0x916D[0], 0x9115[0], 0x9114[0], CloseSession), all sent regardless of rc
```

### supervisor → discovery · v2 · function
`discovery.create(cfg, on_camera: (address, txt) -> None) -> Discovery`: darwin → `MacDiscovery`; else `AvahiDiscovery` if `avahi-browse` works; else `ZeroconfDiscovery`.
`Discovery.start()` (records `networks.interfaces()`, then backend `_start`), `.announce()` (re-publish; camera opens PTP/IP only after hearing an announcement), `.alive() -> bool` (False when the backend failed, the interfaces changed, or `LOCAL_NETWORK_RECHECK` after a Mac refusal → `Supervisor.check_discovery` calls `.restart()` = close + start, at most every `DISCOVERY_RETRY`, errors logged; it also does the first start; announcing follows on the next idle tick), `.close()`, `.denied() -> bool` (Mac: Local Network refused within last `LOCAL_NETWORK_RECHECK`).
`on_camera` called from a discovery thread only for records whose `tid.canon.com` == our GUID and whose address is in `cfg.networks`.
Avahi exception: `avahi-publish-service` publishes where avahi-daemon is configured to; browse results are still filtered by `networks` (stated in `defaults.toml`).

### discovery(Mac) → dnssd · v2 · function
`Service(on_denied)`; `.failed: Event` (mDNSResponder lost or service thread raised → recreate); `.close()` releases refs and the wake pipe, also after the thread died. At most `DNSSD_MAX_RESOLVES` resolves in flight. `Registration(service, name, port, txt, if_indexes).start()/.stop()`. `Browser(service, if_indexes, on_found(address, txt)).start()`. All DNS-SD calls run on the service thread via `Service.call`; `kDNSServiceErr_PolicyDenied (-65570)` → `on_denied`, not fatal.

### menubar → supervisor · v2 · in-process thread
`supervisor(arguments()) -> (Supervisor, QueueListener)`; `Thread(target=sup.run)`. Reads `sup.status.snapshot: Snapshot` (`state`, `file`, `received`, `last_session: LastSession(time,count)`, `error`, `refused`), `sup.discovery.denied()`, `sup.cfg`, `sup.hosts`. Writes only `sup.stop.set()`. Thread dead → `State.DOWN`.

### menubar → macOS · v2 · function
`localnet.request(host)` (UDP `connect` to a known camera, port `PROBE_PORT`, no traffic) → macOS shows its Local Network permission prompt at launch. `services.open_at_login() -> problem: str | None` (`SMAppService.mainAppService`, bundle only). Bundle id `consts.BUNDLE_ID` = `com.thomergil.cannonball`; Info.plist has `NSLocalNetworkUsageDescription`, `NSBonjourServices`, `LSUIElement`, `SUFeedURL` = `consts.UPDATE_FEED`, `SUPublicEDKey` = `consts.UPDATE_PUBLIC_KEY`, `SUEnableAutomaticChecks`, `SURequireSignedFeed`, `SUVerifyUpdateBeforeExtraction`; `en.lproj/InfoPlist.strings` required. `updates.updater() -> SPUStandardUpdaterController | None` (None outside the bundle or if Sparkle fails to load; no delegates).

### user → cannonball · v2 · cli
`python -m cannonball [-c PATH] [--once] [-v] [--install-service]` (`cannonball` script in the PyPI package `cannonball-eos`); unknown args ignored (Finder adds its own). `--once`: exit after first completed session. `--install-service` (`systemd.install`): writes `cannonball/cannonball.service` to the user's systemd dir with `@PYTHON@` = `sys.executable`, then daemon-reload, enable, restart (`Restart=always`); a missing or failing systemctl is a one-line exit.
Files: `state_dir/{guid,hosts.json,local-network-alert-shown,partial}` (`hosts.json`: at most `REMEMBERED_HOSTS` found addresses, filtered by `networks` on load); log `~/Library/Logs/cannonball.log` (mac) or `state_dir/cannonball.log`; images `Download.folder(capture)/<file_name>`, temp `consts.PARTIAL_NAME` = `.<name>.cannonball`, opened exclusive (`xb`), path recorded in `state_dir/partial` while written: removed on failure, and by the next session's `remove_partial` after a kill; an existing same-name temp is replaced only if untouched for `stall_timeout`, else the session fails (another writer), mtime = capture time, same name + different size → `<stem>_N<ext>`; no download unless `size + reserve_mb` is free.

### build → release · v3 · shell
`scripts/build-macos.sh` → `dist/Cannonball-<version>.dmg` + `.sha256`. Env: `SIGNING_IDENTITY` (default Developer ID from keychain, else ad-hoc), `NOTARY_KEY_PATH`+`NOTARY_KEY_ID`+`NOTARY_ISSUER_ID` → notarize + staple, `UV`. `RELEASE=1` requires Developer ID + notary key + the Sparkle EdDSA key in the login Keychain (account `consts.UPDATE_KEY_ACCOUNT`; `generate_keys -p` must equal `consts.UPDATE_PUBLIC_KEY`), and writes `dist/appcast.xml` (`generate_appcast --download-url-prefix consts.RELEASE_DOWNLOAD`; fails without the `sparkle-signatures` block). Python `PYTHON_VERSION` (3.14.10) from python-build-standalone via uv; Sparkle `SPARKLE_VERSION` + `SPARKLE_SHA256`. Release assets, every release: `Cannonball-<version>.dmg` + `.sha256` (appcast enclosure), `Cannonball.dmg` + `.sha256` (README links `releases/latest/download/`), `appcast.xml` (`SUFeedURL` points at the latest release's copy; if the latest release has no `appcast.xml`, Mac updates stop). Publishing a GitHub release runs `.github/workflows/publish.yml`: `uv build` → PyPI `cannonball-eos` (trusted publishing, environment `pypi`). Homebrew: `thomergil/homebrew-tap` `Casks/cannonball.rb` needs the new version + dmg SHA-256 each release; `auto_updates true`.

## Rules
- **ack-after-durable-save** *(error)* — `0x91F0`/`0x9177` for a handle are sent only after its file is flushed (`worker.flush_to_disk`: F_FULLFSYNC on macOS, fsync fallback where unsupported), renamed into place and its folder flushed, or an identical-size file already exists; size ≤ 0 is never saved or acked. _Check: `worker.Session.transfer`; `tests/test_worker.py`._ _History: 2026-11-19-canon-session-protocol._
- **restart-image-from-zero** *(error)* — every image download starts at offset 0 into a newly created temp file; no resume across sessions or connections. _Check: `worker.Session.download`; fakecam never answers a first read at offset ≠ 0._ _History: 2026-11-19-canon-session-protocol._
- **wire-matches-golden** *(error)* — bytes sent on both PTP/IP connections equal `tests/golden/*` and the pinned reference client; a deliberate sequence change needs a real-camera check, re-recorded golden files and a History entry. _Check: `pytest tests/test_golden.py tests/test_differential.py`._ _History: 2026-11-19-canon-session-protocol, 2026-11-20-mit-rewrite-verification._
- **no-gpl-code** *(error)* — no GPL-licensed code in the tree; the reference client is fetched at pinned URL+SHA-256 into gitignored `tests/.reference/`. _Check: `.gitignore`; grep for vendored sources; `REF_SHA256` in `tests/test_differential.py`._ _History: 2026-11-20-mit-rewrite-verification._
- **worker-isolation** *(error)* — PTP/IP traffic happens only in the spawned worker process; supervisor/menubar reach the worker only via `worker.main` as process target and its exit-code constants, never import or call `ptpip`/`canon`; one worker at a time. _Check: grep `ptpip`/`canon`/`Session` in `__main__.py`, `menubar.py`._ _History: 2026-11-19-canon-session-protocol._
- **no-camera-port-probes** *(error)* — reachability is ICMP ping only; TCP to the camera port only via `ptpip.Client` in the worker. _Check: grep `create_connection`/`socket(` outside `ptpip.py`, `localnet.py`, `dnssd.py`._ _History: 2026-11-19-canon-session-protocol._
- **network-scope** *(error)* — announce, browse and accept camera addresses only on `cfg.networks` (default private ranges); out-of-scope addresses logged and dropped. _Check: `Hosts._on_our_networks`, `Discovery.found`, `networks.interfaces()` users; Avahi exception above._ _History: 2026-11-19-discovery-bonjour._
- **mac-mdns-via-mdnsresponder** *(error)* — on macOS, announce/browse only through `dnssd` (mDNSResponder); never python-zeroconf's own responder; no listening sockets. _Check: `discovery.create`._ _History: 2026-11-19-discovery-bonjour._
- **announce-on-wake** *(error)* — when a known host turns reachable and no worker runs, re-announce before connecting; re-announce every `announce_interval` while idle. _Check: `Supervisor.check_host`, `Supervisor.tick`._ _History: 2026-11-19-discovery-bonjour._
- **supervisor-in-app** *(error)* — macOS: supervisor runs in the user-opened app process; no launchd/SMAppService agent; login via `SMAppService.mainAppService` only. _Check: `services.py`, `app.py`._ _History: 2026-11-20-macos-local-network._
- **alert-once** *(error)* — Local Network alert shown at most once until a session reaches RECEIVING (flag `state_dir/local-network-alert-shown`, cleared only then), never within `LOCAL_NETWORK_GRACE` of launch; the only other modal is the config-error alert before quitting; both go through `menubar.alert`. _Check: `MenuBar.check_local_network`, `menubar.main`._ _History: 2026-11-20-macos-local-network._
- **one-installed-copy** *(warn)* — build leaves no extra registered `.app` copies (lsregister -u, delete). _Check: tail of `scripts/build-macos.sh`._ _History: 2026-11-20-macos-local-network._
- **status-single-writer** *(error)* — `Status` is written only by the supervisor thread (own state + worker events); menubar reads snapshots. _Check: grep `status.set`/`status.event`._ _History: 2026-11-20-simplicity-scope._
- **quiet-menu-bar** *(error)* — status item length is `NSSquareStatusItemLength`; `MenuBar.show` sets the button image only when `menubar.icon(state, reduce_motion)` changes; the frame timer (only other image setter) runs only while `Icon.SHIMMER` is shown (RECEIVING, Reduce Motion off). _Check: `menubar.icon`, `MenuBar.show`; `tests/test_menubar.py`._ _History: 2026-11-23-menubar-icon._
- **minimal-menu** *(error)* — menu = status line, optional detail line, Open Pictures Folder, About Cannonball, Check for Updates… (built app only), Quit; icon has no count; no pause, recent files, or extra commands. _Check: `MenuBar.initWithSupervisor_listener_`._ _History: 2026-11-20-simplicity-scope, 2026-11-23-sparkle-updates._
- **config-single-source** *(error)* — every tunable and its default lives only in `defaults.toml` (user file overrides existing keys only); fixed shared identifiers/strings in `consts.py`, protocol numbers in `canon.py`/`ptpip.py`, other literals as named module constants; no inline magic values. _Check: reader judgment; `tests/test_config.py`._ _History: 2026-11-20-simplicity-scope._
- **pinned-wheels-only** *(error)* — every Python dependency pinned by SHA-256 in `requirements*.txt`, installed with `--require-hashes --only-binary :all:`; no sdist-only dependency; every non-Python artifact (Sparkle, test reference client) pinned by URL/version + SHA-256 in the script that fetches it, except the Python runtime: version pinned, uv verifies its checksum; CI actions pinned by commit. _Check: `requirements*.txt`, `.github/workflows/test.yml`, `scripts/build-macos.sh`, `tests/test_differential.py`._ _History: 2026-11-20-dependencies-supply-chain, 2026-11-23-sparkle-updates._
- **no-upward-dependency** *(error)* — `ptpip`, `canon` import no other `cannonball` module; `worker` imports none of `__main__`, `discovery`, `menubar`; only `menubar` imports `__main__`. _Check: grep `from \.` in `cannonball/`._

## Intent
- **send-queue size ("Transferring X/Y")** — owner wants X/Y; today menu shows "Transferring N" (received + current). Camera offers one image at a time and has not been seen to state a queue size; 0xC1B1 is not a count. Delta: worker logs non-property GetEvent records at DEBUG (`Session.log_data`) to find it; nothing decoded yet. Do not use card listings: their count is every image on the card, not the send queue. _History: 2026-11-19-canon-session-protocol._
- **other EOS models** — README invites reports; only the 90D verified (protocol trace source: EOS 250D). Delta: no model-specific code paths exist.
