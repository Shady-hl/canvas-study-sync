# OAuth setup and deployment limits

## Current status

The beta implements Canvas's documented native authorization-code flow, one-time state validation, token exchange and in-memory refresh. It has synthetic security tests. It has not been exercised against an administrator-approved live Canvas integration. Automatic Canvas syncing is unavailable until private administrator configuration is supplied. The window also offers local-file study packs without Canvas sign-in; see the [student quickstart](QUICKSTART.md).

This direct native flow is for a trusted administrator/developer installation on a machine they control. **Do not distribute a developer-key client secret to students, embed it in an installer, or commit it.** This repository does not include a hosted token-exchange service or a general consumer deployment. For a broadly distributed application, the institution must approve and operate a suitable secret exchange/provisioning architecture, followed by a deployment security review. Adding the code alone does not activate an approved integration.

Canvas's [OAuth overview](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth.md) specifies OAuth for applications used by others and administrator-issued developer keys for hosted Canvas. The [endpoint documentation](https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints.md) documents the native out-of-band redirect `urn:ietf:wg:oauth:2.0:oob`. This implementation does not assume undocumented public-client or PKCE support. If your Canvas deployment requires another redirect architecture, stop and implement that approved integration instead.

## Administrator prerequisites

1. Obtain approval for this purpose and the data involved. No request is made to BI or another institution by this repository.
2. Create an appropriately restricted developer key, with only required read scopes. Enable the key for the approved users/installation and confirm native out-of-band authorization is supported.
3. Provision its configuration directly on the trusted administrator/developer machine, outside both the source checkout and study archive. Do not ask end users to obtain or paste developer secrets.

The private JSON object needs `canvas_origin` (exact HTTPS origin), a numeric string `client_id`, `client_secret`, and a nonempty `scopes` array. Every scope must have Canvas's `url:GET|/api/v1/...` form. Non-GET scope forms are rejected locally; Canvas validates the actual scope identifiers. The administrator must restrict the developer key itself as well as the requested scopes.

Use the exact scope identifiers in your Canvas endpoint documentation. The sync needs read access to the current user's profile, active courses/course details, syllabus, modules and module items, pages, assignments, discussions/announcements, quiz descriptions, file listings and file metadata. Reduced scopes produce coverage gaps and permission notices; there is no automatic escalation to broader access.

On Mac/Linux, the private file must be owned by the current user and inaccessible to group/others (`chmod 600` on the provisioned file). On Windows, the administrator must use an owner-only NTFS ACL; this tool does not validate Windows ACLs. The source release checker rejects common private OAuth configuration names, but it cannot recognize every possible secret filename.

## Launch a trusted installation

Pass the provisioned configuration path, which is not itself a credential. Example on Mac/Linux:

```bash
python3 -B canvas-sync/resync_app.py --oauth-config "/private/admin-provisioned/oauth-private.json" --archive "/path/to/Canvas-Archive" --launch
```

On Windows:

```powershell
python -B .\canvas-sync\resync_app.py --oauth-config "C:\Private\oauth-private.json" --archive "D:\Study\Canvas-Archive" --launch
```

Alternatively, an administrator can set `CANVAS_OAUTH_CONFIG` to the private file path in the launch environment. The path is passed to the detached launcher; the client secret is never a command argument. Reopening an already-running window reuses that window's configuration. Restart the background window before changing configuration.

Start sign-in, continue to Canvas, approve the requested access, then copy the **complete address** from Canvas's native completion page into the local response field. It must contain the matching `state` and a `code`, on the exact configured HTTPS origin and `/login/oauth2/auth` path. Passwords, cookies, arbitrary codes, duplicate parameters, foreign origins, expired states and reused responses are refused. An invalid attempt consumes the state; begin again. Authorization denial is handled as a failed sign-in without revealing Canvas response details.

The worker receives a private OAuth payload in its environment, never in command arguments. It refreshes expiring access tokens against the same Canvas origin, stops on authentication failure, and does not save access/refresh tokens to disk or timing logs. The administrator's configuration remains on disk; runtime token handling does not erase that file. Memory and environment variables remain accessible to sufficiently privileged local software. OAuth exchanges are outside the sync's GET-request timing counter and do not automatically retry rate-limited sign-in requests.

## Personal development only

Where your institution permits personal API testing, the explicit development launcher option can expose manual modes:

```bash
python3 -B canvas-sync/resync_app.py --development-auth --auth token --archive "/path/to/Canvas-Archive" --launch
```

Manual token/session modes are not a supported way to onboard other users. Disabled token creation is not approval to substitute a session cookie. The public default refuses these modes even if a modified browser request tries to select them. No cookie extraction or login-control bypass is included.

## Activation still requires external approval

Tests and GitHub checks cannot grant institutional permission, create an approved developer key, validate a real installation, or guarantee freedom from account/legal consequences. Keep this labeled a developer preview until the approved live integration and deployment review are complete.
