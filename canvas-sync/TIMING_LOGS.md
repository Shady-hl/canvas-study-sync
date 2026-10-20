# Resync pace and timing history

Open Canvas Resync.app on Mac or OPEN_RESYNC.cmd on Windows. Move the finish-time slider to give the sync more time and increase spacing between requests. The fastest setting keeps the previous 0.6-second interval; the initial setting uses 2 seconds and the gentlest setting uses 5 seconds. Every setting uses one request at a time and honors server backoff. The estimate is a range, not a deadline. New downloads, first-time setup, throttling, sleep and network changes can extend it.

The estimate starts with the previous archive report when available. Later runs use up to five recent comparable runs, separate full verification from ordinary updates, and account for changes in the local source count. It does not perform a preliminary Canvas scan. It cannot know how much new material Canvas will return before the run.

Automatic Canvas syncing requires administrator-configured OAuth. Local-file builds record operation=local and auth_mode=none, with zero Canvas requests; they do not become samples in the Canvas sync estimate history. OAuth sign-in and refresh exchanges are not included in the Canvas/download GET-request counter; their credentials, codes and client secrets are never timing features.

## Local history for later analysis

Expand **Timing history** in the window to see the storage folder and optionally label the connection as Wi-Fi, Ethernet or hotspot. This label is supplied by you; the app does not inspect Wi-Fi names, network addresses or location.

History stays in the computer's local application data, outside the OneDrive archive:

- Mac: `~/Library/Application Support/CanvasStudySync/<archive-id>/runs/`
- Windows: `%LOCALAPPDATA%/CanvasStudySync/History/<archive-id>/runs/`

Each run creates a UTF-8 JSONL file, one JSON object per line. Full observations are retained across runs for later export or modelling. A separate `timings.json` keeps the latest 20 run summaries and selected pace for the current estimator. Include the timing folder in your private backup if you intend to keep it for years. The app does not upload timing data.

| Event | Features |
|---|---|
| `run_started` | Schema/app version and code fingerprint, OS/release/architecture, Python version, selected request interval, full-verification option, optional connection label, starting estimate and its sample count, known workload counts, request/time limits |
| `request` | Sequential request ID, coarse resource type, salted resource key, Canvas/attachment-host category, attempt and redirect number, start offset, response-header latency, time through response close, bytes read/declared length, HTTP status, numeric Canvas request cost and remaining budget when supplied, connection/read error category, pacing wait and configured interval |
| `stage` | Canvas/local-work phase or server pause, cumulative request count, elapsed time, pacing/backoff totals, rate-limit/low-budget/network/server-error counts, wait-until time and stop reason |
| `local_source` | Source index/count, file type, file size/modification time when available, salted local source key and time observed between progress messages |
| `run_finished` | Outcome/exit code, total/setup/Canvas/local times, request/backoff counts, available file-download/cache/hash counts, course/source counts and extraction/cache/failure counts |

Resource keys are stable on this computer for the same path, using a locally generated salt. Query values, signed download URLs, raw course/file names, response bodies, request/response headers and credentials are excluded. Never add a cookie to a timing dataset. Logs may still reveal study activity and computer details; keep them private.

Request time includes streaming until the response closes; header latency is separately recorded. A timed-out/failed request is logged with an error category. Byte counts mean bytes the script read, not wire bytes. Source timings are observations between progress messages, and can include buffering or following local work; they are not exact extraction CPU timings. Interrupted runs may lack a final event. Training a model should distinguish these cases, retries, full verification, cache hits, and server-imposed waits. This version collects features and uses recent-run estimates; it does not train an ML model.

## Visible limits and background operation

The window keeps throttling/low-budget notices visible after later log output. Persistent throttling stops the entire sync after the request's six attempts; it does not move on to another course. The window also sets a 5,000-request limit and a four-hour limit on starting requests. Completed files remain available after a stop; an individual in-flight download may finish after the time limit. These controls do not establish institutional permission.

Once the page shows a running sync, closing the tab or browser is fine. Keep the computer awake and connected. Timing-log failures are reported without changing a successful sync result.
