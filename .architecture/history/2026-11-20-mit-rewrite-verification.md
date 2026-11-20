<!-- AI-only. Terse facts. -->
# MIT rewrite and wire-level verification — 2026-11-19..20

Owner intent: release as MIT, macOS + Linux, clean README, credit sources, modest claims.

## Decisions
- First version (eos-autosend, GPL-3.0) vendored epsimatic/canon-wifi-downloader's `itu2_ptpip.py`. Kept outside this repo's main line: `gitlab` remote master / local branch `gpl-snapshot` @3e9b77b (incl. `docs/NOTES.md` research notes and `docs/sources/`).
- Replaced by Cannonball's own PTP/IP client (`ptpip.py`, from CIPA DC-X005) and Canon layer (`canon.py`). Reason: MIT release.
- Proof of equivalence, two independent checks:
  - `tests/golden/*.cmd|.event`: bytes the old stack sent to the fake camera per scenario (recorded by old `docs/make_golden.py`). Pins op sequence, chunking, ack order independent of current code.
  - `tests/test_differential.py`: reference client downloaded at pinned commit + SHA-256 into gitignored `tests/.reference/`, driven by the same worker logic; bytes, files, acks must match. Skips if offline.
- `tests/fakecam.py` written from the protocol, independent of `ptpip.py`; models observed 90D behavior (one image at a time, re-offer unacked, stream-only reads).
- README credits protocol source, libgphoto2, CIPA, libraries; states only EOS 90D tested.

**Touches:** part ptpip, part canon, part tests, rule no-gpl-code, rule wire-matches-golden, `tests/test_golden.py`, `tests/test_differential.py`, `tests/fakecam.py`, `README.md`
