# Contributing

Contributions for permitted noncommercial study use are welcome under [LICENSE](LICENSE). Keep the tool local and read only, preserve incremental updates, and follow [USE_POLICY.md](USE_POLICY.md).

Run `python -B -m unittest discover -s canvas-sync/tests` and `python -B tools/check_release.py` before proposing changes. Use synthetic fixtures and mocked network responses. Never include course downloads, personal settings, archives, logs or credentials in a pull request. Explain the user-visible behavior and meaningful validation.

Dependency or security reports should follow [SECURITY.md](SECURITY.md). Do not add automatic browser-cookie extraction, access-control bypasses, submissions, grade writes, secret storage in commands, external analytics, or background uploads. Unattended authentication requires an institution-approved method.

Unless a contribution explicitly states otherwise, submitting it for inclusion means offering it under the same repository license. Third-party code must retain its own required notices and compatible permissions. Do not contribute material you lack the right to share.
