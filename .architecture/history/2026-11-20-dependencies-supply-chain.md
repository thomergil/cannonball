<!-- AI-only. Terse facts. -->
# Dependencies and supply chain — 2026-11-20

## Decisions
- Runtime deps: `ifaddr`, `zeroconf`, PyObjC Cocoa + ServiceManagement (macOS). All pinned by SHA-256 (PyPI hashes), wheels only (`--require-hashes --only-binary :all:`). CI tests Python 3.11 and 3.15 on Ubuntu + macOS; actions pinned by commit.
- macOS app Python: 3.14 from python-build-standalone via uv (checksum-verified); PyInstaller from the dev requirements.
- Config format TOML via stdlib `tomllib`; tests write TOML with `tomli-w`.
- Menu bar written directly against PyObjC.

## Dead ends (do not retry)
- PyYAML (old `config.yaml`): no Python 3.15 wheel → dropped for TOML.
- rumps (old menu bar): sdist only, build deps unverifiable → dropped for plain PyObjC.

**Touches:** part config, part menubar, part packaging, rule pinned-wheels-only, `requirements.in`, `requirements.txt`, `requirements-dev.txt`, `.github/workflows/test.yml`, `scripts/build-macos.sh`
