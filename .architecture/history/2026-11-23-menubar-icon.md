<!-- AI-only. Terse facts. -->
# Menu bar icon — 2026-11-20, 2026-11-23 (contract v3, Cannonball 0.1.1)

## Decision (2026-11-23)
- `menubar.icon(state, reduce_motion) -> Icon` (pure; `Icon` StrEnum IDLE, STILL, SHIMMER, DOWN):

| State | Icon |
|---|---|
| RECEIVING (photos arriving) | SHIMMER; STILL under Reduce Motion |
| CAMERA (on network, connecting) | STILL: shimmer band stopped at `SHIMMER_STILL` = 0.5 (share of the camera's width) |
| WAITING | IDLE: plain camera |
| DOWN | DOWN: warning triangle |

- `MenuBar.show(wanted)` sets the button image only when the icon changes; runs the frame timer (NSTimer) (`SHIMMER_STEP` 0.05 s, 20 fps) only while SHIMMER is shown. Refresh timer (0.4 s) calls `show` every tick; calls with the icon already shown are no-ops.
- Status item fixed width: `NSSquareStatusItemLength` (camera symbol 19 pt, triangle 17 pt fit the 22 pt menu bar).

## Why
In 728ef14 (2026-11-20) the icon shimmered whenever the camera was connected (CAMERA or RECEIVING) on an `NSVariableStatusItemLength` item. On the owner's Mac the camera stayed connected all day: Cannonball held ~4% CPU; WindowServer logged `_CGXPackagesSetWindowConstraints: Invalid window` 539 times per 15 s, 259 with Cannonball paused by SIGSTOP (~20/s from Cannonball). Each `setImage_` on a variable-length item re-runs menu bar layout; in 728ef14 the refresh timer also re-set the same image every tick.

## Dead ends (do not retry)
- Lower frame rate only: still animates all day while the camera idles on the network.
- Variable-length status item: one menu bar relayout per frame.

**Touches:** part menubar, rule quiet-menu-bar, rule minimal-menu, `cannonball/menubar.py`, `tests/test_menubar.py`
