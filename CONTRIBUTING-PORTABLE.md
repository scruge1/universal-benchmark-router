# Contributing Benchmark Evidence

The router accepts evidence, not reputation or model names alone. A benchmark
contribution is eligible only when its exact task pack, model artifact,
fine-tune, quantization, context, sampling, engine, hardware, placement,
offload, power, workload, quality checks, and restoration result are recorded.

## Trust boundary

- Keep every private signing key outside the repository.
- The registry operator enrolls public keys through a separate reviewed trust
  process. The CLI cannot enroll or generate a trusted key.
- Git or another transport preserves and reviews bytes. It does not approve
  benchmark truth.
- A contributor cannot acknowledge their own result.
- A dashboard or host adapter cannot edit evidence or upgrade its authority.
- Never edit an accepted identity. Submit a new identity that names the
  superseded evidence and let policy decide which record is current.

## Contribution order

1. Obtain the exact capability contract, task pack, benchmark request, and
   current public-key map from the registry maintainer.
2. Run only the request's declared test cells. Retain raw evidence outside the
   compact result and bind it by SHA-256.
3. Sign the contribution and result envelope with the contributor key.
4. Validate the request, contribution, and result locally.
5. Submit the saved documents and raw-evidence location for review.
6. An independent validator checks the signature, task pack, artifact,
   configuration, raw evidence, system class, and replay state, then signs an
   acknowledgement.
7. Intake order is request, contribution, result, acknowledgement. The
   registry rejects a downstream document if its exact dependency is absent.
8. Audit the registry. Build catalogs and route decisions only from accepted
   records that meet the multi-contributor policy.

## Local commands

Each document type has `validate-*` and `ingest-*` commands:

```text
validate-request          ingest-request
validate-contribution     ingest-contribution
validate-result           ingest-result
validate-acknowledgement  ingest-acknowledgement
```

Use `universal-router <command> --help` for its exact file arguments. Validation
does not write. Intake stores canonical JSON by SHA-256, records an acceptance
event, and commits one exclusive identity claim. Exact replay and same-identity
conflict are errors.

## Pull-request review

A contribution pull request must show:

- the exact request and task-pack identities;
- the model, revision, fine-tune, quantization, engine, context, temperature,
  top-p, KV format, topology, offload, power, and concurrency settings;
- hardware and site identities with the disclosure policy applied;
- per-case task records, reliability, performance, energy, and restoration;
- contributor signature and an independent acknowledgement;
- no private key, access token, endpoint credential, personal data, or private
  prompt.

CI should run strict JSON parsing, every contract validator, registry replay in
a fresh directory, the integrity audit, the portable dependency boundary, the
focused tests, Ruff, and bytecode compilation. CI is a verifier. It does not
publish, enroll keys, run models, or operate contributor hardware.
