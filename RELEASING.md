# Release Procedure

1. Merge changes to `main` only after all required CI jobs pass.
2. Confirm the worktree is clean and local `main` equals `origin/main`.
3. Confirm `[project].version` in `pyproject.toml` is the intended version.
4. Run the source-manifest, tests, Ruff, bytecode, boundary, fixture, build, and
   archive-content gates locally.
5. Create and push the exact annotated tag `vX.Y.Z` at the accepted main commit.
6. Observe the tag-only `release` workflow. Do not create assets by hand while
   it is running.
7. Download both release assets. Verify their SHA-256 values and run
   `gh attestation verify <asset> --repo scruge1/universal-benchmark-router`.
8. Install the downloaded wheel into a clean environment and run
   `universal-router --help`.
9. Record the tag, commit, workflow run, asset hashes, attestation verification,
   and clean-install result in the controlling release receipt.

A failed release run remains evidence. Fix the cause through a new reviewed
commit and use a new version. Do not move or reuse a published version tag.

PyPI is outside this procedure until a separate Trusted Publisher is configured
and verified. Never add an API token to repository secrets as a shortcut.
