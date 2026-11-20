<!-- AI-only. Terse facts. -->
# macOS app and Local Network privacy — 2026-11-20

Owner intent: "No Terminal, no shell, just an elegant thing in the background that first asks me for permission and then sits on the Menu Bar, just like the original Canon app." Error modal at most once ("Once. Not recurring.").

## Decisions
- One app (`Cannonball.app`, `LSUIElement`), supervisor on a thread inside it; login via `SMAppService.mainAppService`. Reason (Apple TN3179): macOS asks for Local Network permission only for an app the user opens.
- Trigger the system prompt at launch by UDP `connect` to a known camera address (no traffic sent) — `localnet.request`.
- Detect refusal two ways: DNS-SD `kDNSServiceErr_PolicyDenied` (-65570) and worker connect `EHOSTUNREACH` while the camera answers pings. Show one alert (Open System Settings / Restart), after a 30 s grace for the system prompt; flag file prevents repeats; cleared after a session reaches RECEIVING.
- Bundle id `com.thomergil.cannonball`; standard `en.lproj/InfoPlist.strings` (macOS reads the app name and usage text for the Local Network prompt from it).
- Build script unregisters (lsregister -u) and deletes its intermediate `.app` copies.

## Dead ends (do not retry)
- launchd agent via `SMAppService.agentServiceWithPlistName_` (first MIT layout: separate daemon + menu bar reading `status.json`): macOS denied the agent Local Network access without showing a prompt.
- Testing from Terminal: Terminal-run code is auto-allowed; proves nothing about the app (why the first prototype worked).
- Multicast UDP send as permission probe: not a valid probe (owner-verified; reason not recorded).
- Bundle id `com.thomer.cannonball`: got a stuck, hidden Local Network state; do not reuse.
- Several installed copies of one bundle id break Local Network privacy decisions (FB15568200; exact effect not recorded).

## Environment notes
- Owner's Mac: system network-extension store full (thousands of mutt-desktop test fixture entries), so new decisions fail to save; worked around with `AllowedEthernetLocalNetworkAddresses`/`AllowedWiFiLocalNetworkAddresses`. Not a Cannonball bug.
- In zsh `log` is a builtin; use `/usr/bin/log` for unified logs.

**Touches:** part menubar, part packaging, interface menubar→macOS, interface menubar→supervisor, rule supervisor-in-app, rule alert-once, rule one-installed-copy, `cannonball/menubar.py`, `cannonball/localnet.py`, `cannonball/services.py`, `cannonball/app.py`, `packaging/macos/Cannonball.spec`, `scripts/build-macos.sh`
