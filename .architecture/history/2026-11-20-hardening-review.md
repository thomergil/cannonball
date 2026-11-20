<!-- AI-only. Terse facts. -->
# Hardening after full review — 2026-11-20 (contract v2)

Trigger: owner asked for a full code review of everything built. Audit panel (purist, skeptic, conformist, plain, security) found a critical path traversal in 0.1.0.

## Decisions
- Camera file names are untrusted: `canon.file_name` keeps only a plain last component (`FILE_NAME`), else `IMG_<handle>`; `worker.destination` also requires the resolved path inside `destination` (covers `subfolder`). 0.1.0 wrote `/tmp/cannonball-escape/owned.JPG` in a red test.
- Temp file `.<name>.cannonball`: camera names cannot start with a dot, so a camera-chosen `X.part` can no longer truncate another image's temp file. Owner: no pile of hidden files. Removed on failure; a kill leaves its path in `state_dir/partial`, which the next session removes (one worker per state_dir). Opened `xb`; a same-name temp younger than `stall_timeout` belongs to another writer (second instance, shared NAS folder) and is left alone.
- Durability: F_FULLFSYNC on macOS (fsync leaves data in the drive cache), fsync fallback on ENOTSUP/EOPNOTSUPP/ENOTTY/EINVAL (SMB/NFS/WebDAV refuse it; Go's runtime falls back on ENOTSUP), then folder flush after the rename.
- Free-space floor `download.reserve_mb` instead of per-session caps: the image waits on the camera.
- Op timeout counts from progress, not from any packet; one Start_Data per op; data phase ≤ 16 MiB (bounds `chunk_size_mb`; a Data packet adds its 4-byte transaction id); `canon.events` is a generator.
- Discovery restarts when `networks.interfaces()` changes (login before Wi-Fi joins), and after a dnssd thread failure; `Service.close` owns the wake pipe (closing it from the dying thread made every restart fail with EBADF).
- Config: type + range validated against `defaults.toml`; app shows a config-error alert and logs it, then quits (owner wants a modal when Cannonball cannot work).

## Dead ends (do not retry)
- Sweeping every `.*.cannonball` in the target folder before each download: deletes another instance's live temp file, and misses folders never written again.
- systemd sandbox (`ProtectHome=read-only`, `ReadWritePaths=%h/Pictures`, `StateDirectory=`) in the user unit: fails to start when `~/Pictures` is missing, maps `StateDirectory` differently on older systemd, and needs user namespaces. Reverted for robustness.
- Mutation testing with bytecode caching on: stale `.pyc` reported false survivors; run with `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider`.
