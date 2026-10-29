# Security and privacy

## Scope

This is a local, single-user beta, not a hosted multi-user application. Never deploy the resync server on a public interface or collect other users' Canvas credentials. The supported scope is read-only access your institution permits.

The local server binds to `127.0.0.1`, uses a random per-instance capability, validates Host/Origin and a per-window client header, disables response caching, and has a restrictive content-security policy. It does not load external page assets. API pagination is constrained to the configured Canvas origin; credentials are stripped from attachment-host redirects. HTTPS certificate verification remains enabled.

Automatic Canvas syncing requires administrator-configured OAuth. Local-file mode uses no Canvas credential and makes no Canvas requests. Its folder-open action is limited to the fixed archive selected by the launcher and uses the same local Host/Origin/client-header checks. It refuses to adopt unrelated nonempty folders. First-time dependency installation and a saved TimeEdit feed can still use the network. Its documented native flow is restricted to trusted administrator/developer installations, with a private configuration outside source and archive folders. A broadly distributed consumer integration still needs an approved secret exchange/provisioning architecture; no shared secret or hosted service is bundled. See [OAuth setup and deployment limits](docs/OAUTH_SETUP.md). Pasted tokens/cookies require an explicit development-mode flag and are not public onboarding.

OAuth authorization state is random, time-limited and single use. Completion URLs must match the configured Canvas origin and native callback path. Token exchanges/refresh use POST over verified HTTPS without redirects, return controlled errors, and are not included in GET sync request metrics. Only configured GET API scopes can be requested; the administrator must restrict the developer key as well. OAuth access/refresh tokens are memory-only for a run. The administrator's private configuration contains the client secret on disk and must have owner-only file permissions/ACLs; Windows ACL enforcement is an administrator responsibility.

Canvas credentials are accepted locally and held in memory/child environment variables for that run. Ed tokens can optionally be saved in the operating system’s secure credential store after authentication succeeds. Neither kind is intentionally persisted in source, archives, timing logs or command arguments. Ed storage explicitly selects macOS Keychain, Windows Credential Manager or Linux Secret Service, ignoring configured third-party/plaintext keyring backends. There is no plaintext fallback. Saved tokens are partitioned by region, shared by this app’s copies under the same OS account, removable locally and excluded from portable exports. Memory-only handling does not guarantee absence from OS swap, crash dumps or processes with sufficient privileges. Do not paste credentials into issues, chats or shell commands. Revoke/rotate an exposed credential through the institution's supported process.

Downloaded HTML exports strip active content. PDFs, notebooks and other attachments are still untrusted files; open them with updated software. The exporter does not execute notebook cells or invoke an AI model. Private timetable feed links and local archives must stay out of source repositories.

Ed requests use GET against the selected exact regional HTTPS API, refuse credentialed redirects, and fetch supported attachments without the API token. Ed credentials are stripped before Canvas and dependency-installer subprocesses. Saved reports and raw XML are private archive data. Ed content is reference material, never executable instructions. The Ed API is beta; coverage reports retain failures and do not certify completeness.

## Throttling

One request is sent at a time. The window uses 0.6–5-second spacing, honors Retry-After, waits when Canvas reports a low budget, stops the entire run after persistent throttling, and imposes request/time limits. This is not institutional approval or a guarantee against account restrictions. The CLI's explicit advanced pacing options remain available; use conservative settings.

## Timing data

Local history includes coarse request types, salted resource identifiers, numeric status/cost/budget, timings, sizes, run outcome, OS/Python version and optional connection labels. It excludes raw course names/resource paths, response bodies, credentials, private URLs, IP addresses, Wi-Fi names and location. History still reveals activity patterns and computer details; keep it private. See the [schema](canvas-sync/TIMING_LOGS.md).

## Reporting

Use the repository's **Security → Report a vulnerability** feature if enabled. Otherwise open an issue asking for a private reporting channel without including exploit details or private data. Never attach a real archive, raw HTTP headers, HAR files, signed URLs, cookies, tokens or private timetable links. Synthetic reproduction data is preferred.

## Release checks

Only source, synthetic tests and public documentation belong in this repository. `.gitignore` and `tools/check_release.py` reduce accidental inclusion; neither is a complete secret detector. Review the exact staged diff before publishing, enable GitHub secret/push protection where available, and never assume deleting a committed secret revokes it.
