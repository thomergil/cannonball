<!-- AI-only. Terse facts. -->
# Canon auto-send session protocol — 2026-11-19..20

Facts verified on a real EOS 90D (firmware 1.1.1). Protocol source: epsimatic/canon-wifi-downloader PROTOCOL.md @85b60cd (captures of ITU2 + EOS 250D); op names from libgphoto2 `ptp.h`; session ID 0x41 from libgphoto2 issue #333.

## Decisions
- Follow ITU2's flow exactly: OpenSession 0x1002[0x41] + ITU2 setup ops (`canon.SETUP`); images come as 0xC1AB records in 0x9116 GetEvent data; read with 0x9174 chunks (1 MiB default `download.chunk_size_mb`); ack 0x91F0 + 0x9177; end with `canon.FINISH`. Reason: only flow that transferred on the 90D.
- All setup ops are sent before any response code is checked, and all FINISH ops are sent regardless of response codes, as ITU2 does; golden files pin both.
- Ack only after the file is fsynced and renamed into place. Interrupted image stays on the camera's list and is re-offered next session. Owner: robustness over optimization; re-sending a whole file is acceptable.
- Every image download restarts at offset 0. Owner: "super robustness, even if that means starting interrupted files from scratch."
- Session runs in a killable child process with a heartbeat; supervisor kills after `stall_timeout` (60 s) without a heartbeat update. Reason: camera hangs leave reads blocked; only a process kill is reliable.
- Camera reachability by ICMP ping, `ping_misses` (3) misses before "gone": 90D Wi-Fi drops single pings.
- Size ≤ 0 from the camera: fail session, never save/ack (would mark an image sent with no data).
- Already-saved identical-size file: ack without download (re-offered after a lost ack).

## Dead ends (do not retry)
- libgphoto2's standard flow (session ID 1, GetObject 0x1009 / object listing): hangs or empty listings on the 90D in auto-send mode.
- Resuming a file in a new session at a non-zero 0x9174 offset: 90D never answers, then cancels the transfer and turns Wi-Fi OFF. Within one session the camera answers only a first read at offset 0, then sequential reads.
- Throwaway TCP connects to port 15740 (reachability checks): disturb the camera (owner-verified; exact effect not recorded). Only the worker's real session connects.
- Reading a send-queue count from a card listing: the "216" seen 2026-11-19 was the total on the card, not the send queue.

## Open
- Camera offers one image at a time, next only after 0x9177; no queue size seen. 0xC1B1 RequestAutoSendImages undecoded. Worker logs non-property records at DEBUG to look for it. Blocks "Transferring X/Y" (Intent).

**Touches:** part worker, part canon, part ptpip, interface worker→ptpip, interface worker→canon, interface supervisor→worker, rule ack-after-durable-save, rule restart-image-from-zero, rule worker-isolation, rule no-camera-port-probes, rule wire-matches-golden, `cannonball/worker.py`, `cannonball/canon.py`, `tests/fakecam.py`
