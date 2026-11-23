<!-- AI-only. Terse facts. -->
# Product scope and code standard — 2026-11-20

Owner: "Pursue radical simplicity." "It's a million times better if it just works out of the box instead of mini micro optimizations." Owner does not want implementation questions; decide from ARCHITECTURE.md, `.architecture/history/`, and the code.

## Decisions
- Menu: status line, detail line (last session / last error / problem), Open Pictures Folder, Quit. Icon: camera, animated only while photos arrive; warning triangle after the supervisor thread dies (states: 2026-11-23-menubar-icon). No count next to the icon.
- Quit exits the app, stopping the supervisor and killing any running worker; reopening the app starts them again. Interrupted image is re-sent next time.
- Status lives in memory (`status.Status`), written by the supervisor thread, read by the menu bar. Possible because supervisor and menu bar share one process (see 2026-11-20-macos-local-network).
- Code standard (owner's `.safe_code.json`, not committed): clean, DRY, one source per state with the rest derived, established libraries, config-driven; no hardcoded numbers/strings; constants in `consts.py`, tunables in `defaults.toml`.

## Dead ends (do not retry)
- Pause/resume flag, recent-files submenu, `--dry-run`, `--host` (removed 96b8f06): owner rejected as unnecessary.
- `status.json` + pid file shared between a daemon and the menu bar: removed with the daemon; one source of state in-process.

**Touches:** part menubar, part status, part config, interface menubar→supervisor, rule minimal-menu, rule status-single-writer, rule config-single-source, `cannonball/menubar.py`, `cannonball/status.py`, `cannonball/consts.py`, `cannonball/defaults.toml`
