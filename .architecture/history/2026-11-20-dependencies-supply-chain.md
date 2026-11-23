<!-- AI-only. Terse facts. -->
# Dependencies and supply chain — 2026-11-20, 2026-11-23

## Decisions
- Runtime deps: `ifaddr`, `zeroconf`, PyObjC Cocoa + ServiceManagement (macOS). All pinned by SHA-256 (PyPI hashes), wheels only (`--require-hashes --only-binary :all:`). CI tests Python 3.11 and 3.15 on Ubuntu + macOS; actions pinned by commit.
- macOS app Python: 3.14 from python-build-standalone via uv (checksum-verified); PyInstaller from the dev requirements.
- Sparkle 2.10.1 (2026-11-23): runtime dependency of the macOS app only, non-Python; tarball pinned by version + SHA-256 in `scripts/build-macos.sh` (details: 2026-11-23-sparkle-updates). Rule pinned-wheels-only now also covers non-Python artifacts: pinned by URL/version + SHA-256 in the script that fetches them (as the test reference client already was).
- Config format TOML via stdlib `tomllib`; tests write TOML with `tomli-w`.
- Menu bar written directly against PyObjC.

## Dead ends (do not retry)
- PyYAML (old `config.yaml`): no Python 3.15 wheel → dropped for TOML.
- rumps (old menu bar): sdist only, build deps unverifiable → dropped for plain PyObjC.

**Touches:** part config, part menubar, part packaging, rule pinned-wheels-only, `cannonball/updates.py`, `requirements.in`, `requirements.txt`, `requirements-dev.txt`, `.github/workflows/test.yml`, `scripts/build-macos.sh`
