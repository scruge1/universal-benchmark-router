"""Generate one signed, synthetic, CI-only contribution bundle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_universal_benchmark_exchange import (  # noqa: E402
    acknowledgement_fixture,
    contract_fixture,
    contribution_fixture,
    public_keys_fixture,
    request_fixture,
    result_fixture,
)
from test_universal_model_router import suite_fixture  # noqa: E402
from universal_benchmark_registry import canonical_json_bytes, sha256_bytes  # noqa: E402


def build_bundle() -> dict[str, object]:
    contract = contract_fixture()
    suite = suite_fixture()
    contribution = contribution_fixture(suite)
    request = request_fixture(contract, suite, contribution)
    result = result_fixture(request, contribution, suite)
    contribution = result["contribution"]
    acknowledgement = acknowledgement_fixture(contribution, validator_id="validator-a")
    public_keys = public_keys_fixture("issuer", "contributor-a", "validator-a")
    return {
        "capability-contract.json": contract,
        "suite.json": suite,
        "request.json": request,
        "contribution.json": contribution,
        "result.json": result,
        "acknowledgement.json": acknowledgement,
        "public-keys.json": public_keys,
    }


def write_bundle(output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError("fixture output must be absent")
    output.mkdir(parents=True)
    documents = build_bundle()
    hashes: dict[str, str] = {}
    for name, document in documents.items():
        value = canonical_json_bytes(document)
        (output / name).write_bytes(value)
        hashes[name] = sha256_bytes(value)
    manifest = {
        "schema": "universal-benchmark-ci-fixture-bundle/v1",
        "authority": "synthetic_test_fixture_only_never_routing_evidence",
        "contains_private_key": False,
        "synthetic_contributor_count": 1,
        "minimum_contributors_for_routing": 2,
        "documents": hashes,
    }
    (output / "fixture-manifest.json").write_bytes(canonical_json_bytes(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = write_bundle(args.output.resolve())
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
