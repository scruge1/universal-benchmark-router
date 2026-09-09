# Security Policy

## Report a vulnerability

Use the repository's private security-advisory reporting path. Do not put an
unfixed vulnerability, private key, access token, endpoint credential, private
benchmark prompt, or personal hardware detail in a public issue.

Include the affected commit or release, the smallest reproduction, expected and
observed behavior, and the security boundary that failed. Reports about a local
adapter must name that adapter; this portable repository does not operate GPUs,
models, dashboards, or inference endpoints.

## Supported version

Until a later policy supersedes this file, only the newest GitHub release is
supported. Source snapshots and unmerged branches are development evidence.

## Trust limits

A valid signature proves control of an enrolled key. It does not prove that a
benchmark is accurate. An artifact attestation proves build provenance. It does
not prove that the package is safe. The registry and release gates remain
separate.
