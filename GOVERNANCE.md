# Governance

This repository separates source acceptance, software release, and benchmark
evidence promotion. Passing one boundary does not grant authority at another.

## Source changes

- Change `main` through a pull request.
- Require every declared Linux and Windows CI job to pass.
- Keep `SOURCE-MANIFEST.json` synchronized with the reviewed source tree.
- Do not force-push or delete `main`.
- A merge accepts source bytes only. It does not accept benchmark evidence or
  authorize a software release.

## Releases

- A maintainer creates an exact `vX.Y.Z` tag only after the matching source
  commit passes required checks.
- The tag must equal `v` plus `[project].version` from `pyproject.toml`.
- The tag-only release workflow reruns all verification, builds and scans both
  package formats, attests those exact artifacts, and creates the GitHub release.
- Release provenance identifies the source and workflow. It does not prove that
  the software is defect-free or that benchmark evidence is true.
- PyPI publication is not configured. If added later, use a separately reviewed
  Trusted Publisher and protected environment. Do not store an API token here.

## Benchmark evidence

- CI can validate signed documents but cannot enroll a trusted key or promote
  evidence.
- A contributor cannot acknowledge their own result.
- Routing eligibility requires the declared independent-contributor policy.
- Corrections append a new identity and supersession record. Never rewrite an
  accepted identity.
- The registry operator owns the reviewed public-key map outside the CLI.

## Authority

`GOVERNANCE.md` is the repository policy for source and release changes.
`CONTRIBUTING-PORTABLE.md` is the evidence contribution procedure. The schemas
and executable validators decide document conformance. No document in this
repository grants access to contributor hardware or a local deployment.
