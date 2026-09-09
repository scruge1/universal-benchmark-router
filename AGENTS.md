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
- `GOVERNANCE.md` controls repository source and release changes.
  `CONTRIBUTING-PORTABLE.md` controls benchmark-evidence submission. PRD-156
  remains the upstream product and research authority.

## Agent prompts

- Read `README.md` and `CONTRIBUTING-PORTABLE.md` before changing contracts.
- Read `GOVERNANCE.md` and `RELEASING.md` before changing release paths.
- Do not change sibling projects or local host adapters from this repository.
- Do not treat CI success or a Git merge as benchmark-evidence promotion.
