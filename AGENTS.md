# Universal Benchmark Router

- This repository is the portable benchmark evidence registry and
  configuration router. It is independent of Adam's dashboard and rig.
- Treat `SOURCE-MANIFEST.json`, the four `universal_*.py` modules, the router
  contracts, schemas, tests, and `CONTRIBUTING-PORTABLE.md` as canonical.
- Evidence intake is offline and append-only. Never add endpoint calls, model
  loading, GPU control, trusted-key enrollment, or publication authority to the
  portable core.
- Run the source-manifest, fixture, boundary, test, Ruff, package-build, and
  release-content gates before claiming a change is ready.
- The controlling SSOT remains PRD-156 in the heterogeneous inference control
  plane workspace until this repository receives its own accepted release
  governance.

## Agent prompts

- Read `README.md` and `CONTRIBUTING-PORTABLE.md` before changing contracts.
- Do not change sibling projects or local host adapters from this repository.
- Do not treat CI success or a Git merge as benchmark-evidence promotion.
