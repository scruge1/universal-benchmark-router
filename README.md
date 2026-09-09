# Universal Benchmark Router

This package turns saved, standardized model tests into evidence-backed model
and configuration recommendations. It is independent of any agent harness,
dashboard, inference engine, or GPU layout.

It provides four parts:

- Exact contracts for model variants, fine-tunes, quantization, context,
  sampling, engine settings, hardware, placement, offload, power, workload,
  quality, reliability, and restoration.
- Signed benchmark requests, contributions, results, and independent
  acknowledgements.
- An offline append-only registry that rejects replay, conflicting identities,
  missing dependencies, invalid signatures, and changed stored bytes.
- A deterministic router that answers a structured task and configuration
  request from eligible saved evidence.

It does not download or load models, call inference endpoints, operate GPUs,
control a dashboard, or publish a route. A local adapter can consume a saved
decision, but it must apply its own authorization, freshness, health, and
rollback controls.

## Install

Install from a checked-out source tree:

```console
python -m pip install .
```

After a GitHub release exists, download its wheel and verify its provenance
before installation:

```console
gh attestation verify universal_benchmark_router-0.2.0-py3-none-any.whl \
  --repo scruge1/universal-benchmark-router
python -m pip install universal_benchmark_router-0.2.0-py3-none-any.whl
universal-router --help
```

The package requires Python 3.10 or newer and `cryptography` 41 or newer.

Export the bundled standard suite and capability contract after installation:

```console
universal-router export-contracts --output-dir ./router-contracts
```

## Validate and store evidence

Initialize a registry once:

```console
universal-router registry-init --root ./evidence-registry
```

Validate a signed request without writing it:

```console
universal-router validate-request \
  --request request.json \
  --capability-contract benchmark-capability-contract.json \
  --public-keys public-keys.json
```

Replace `validate-request` with `ingest-request` and add
`--root ./evidence-registry` to commit the verified request. Contributions must
be ingested before their result and acknowledgement. Run
`universal-router registry-audit --root ./evidence-registry` after intake.

Every command returns compact JSON. A rejection exits with status 2 and writes
a structured error to standard error.

## Compile evidence and select a route

Compile only accepted contributions and acknowledgements from an audited
registry. The timestamp is explicit so the output is deterministic and
reviewable:

```console
universal-router compile-catalog \
  --root ./evidence-registry \
  --suite ./router-contracts/standard-task-suite-v1.json \
  --generation 1 \
  --compiled-at 2026-09-09T10:00:00Z \
  --output ./catalog-1.json
```

Join qualified community task evidence to saved performance profiles for the
hosts available to the caller:

```console
universal-router route \
  --request ./model-route-request.json \
  --suite ./router-contracts/standard-task-suite-v1.json \
  --catalog ./catalog-1.json \
  --profile ./rig-01-profile.json \
  --profile ./rig-02-profile.json \
  --output ./route-decision.json
```

The decision says `selected` or `no_eligible_route` and lists every exclusion.
It is saved-data evidence only. It does not contact the selected endpoint,
load a model, or publish a live route. Output paths are absent-only so a retry
cannot overwrite an earlier candidate.

See `CONTRIBUTING-PORTABLE.md` for the complete order and trust rules.

## Verify this repository

Run the same offline gates used by continuous integration:

```console
python tools/source_manifest.py --verify
python -m pytest -q
ruff check .
python verify_portable_router_boundary.py --root .
```

The workflow also creates a fresh, signed synthetic fixture and proves that it
can pass intake without becoming routing evidence. The fixture has one test
contributor while routing requires two independent contributors. It contains
no private key. CI builds and scans the wheel and source archive, but it does
not publish either artifact.

See `GOVERNANCE.md` for source, release, and evidence authority boundaries and
`RELEASING.md` for the maintainer release procedure.
