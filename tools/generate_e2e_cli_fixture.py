"""Generate a two-contributor synthetic fixture for installed-CLI testing."""

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
    contribution_fixture,
    public_keys_fixture,
)
from test_universal_model_router import (  # noqa: E402
    profile_fixture,
    request_fixture,
    suite_fixture,
)
from universal_benchmark_registry import canonical_json_bytes, sha256_bytes  # noqa: E402


def build_bundle() -> dict[str, object]:
    suite = suite_fixture()
    contribution_a = contribution_fixture(suite, contributor_id="contributor-a", site_id="site-a")
    contribution_b = contribution_fixture(suite, contributor_id="contributor-b", site_id="site-b")
    return {
        "suite.json": suite,
        "contribution-a.json": contribution_a,
        "contribution-b.json": contribution_b,
        "acknowledgement-a.json": acknowledgement_fixture(
            contribution_a, validator_id="validator-b"
        ),
        "acknowledgement-b.json": acknowledgement_fixture(
            contribution_b, validator_id="validator-a"
        ),
        "public-keys.json": public_keys_fixture(
            "contributor-a", "contributor-b", "validator-a", "validator-b"
        ),
        "route-request.json": request_fixture(suite),
        "local-profile.json": profile_fixture(
            suite,
            name="exchange",
            gpu_count=4,
            quality=0.1,
            goodput=40,
            latency_ms=900,
            energy=5,
        ),
    }


def write_bundle(output: Path) -> dict[str, object]:
    if output.exists():
        raise ValueError("fixture output must be absent")
    output.mkdir(parents=True)
    hashes: dict[str, str] = {}
    for name, document in build_bundle().items():
        payload = canonical_json_bytes(document)
        (output / name).write_bytes(payload)
        hashes[name] = sha256_bytes(payload)
    manifest = {
        "schema": "universal-benchmark-e2e-cli-fixture/v1",
        "authority": "synthetic_test_fixture_only_never_routing_evidence",
        "contains_private_key": False,
        "synthetic_contributor_count": 2,
        "documents": dict(sorted(hashes.items())),
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
