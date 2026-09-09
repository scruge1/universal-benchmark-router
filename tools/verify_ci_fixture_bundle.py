"""Verify synthetic fixture intake without granting routing evidence."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_benchmark_exchange import compile_request_bound_catalog  # noqa: E402
from universal_benchmark_registry import FileRegistry, sha256_bytes, strict_json_bytes  # noqa: E402


EXPECTED_FILES = {
    "capability-contract.json",
    "suite.json",
    "request.json",
    "contribution.json",
    "result.json",
    "acknowledgement.json",
    "public-keys.json",
}
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
)


def _load(path: Path) -> Any:
    return strict_json_bytes(path.read_bytes())


def verify_bundle(bundle: Path) -> dict[str, object]:
    manifest = _load(bundle / "fixture-manifest.json")
    if manifest != {
        "schema": "universal-benchmark-ci-fixture-bundle/v1",
        "authority": "synthetic_test_fixture_only_never_routing_evidence",
        "contains_private_key": False,
        "synthetic_contributor_count": 1,
        "minimum_contributors_for_routing": 2,
        "documents": manifest.get("documents"),
    }:
        raise ValueError("fixture manifest shape or authority drift")
    if set(manifest["documents"]) != EXPECTED_FILES:
        raise ValueError("fixture document set drift")
    actual_files = {path.name for path in bundle.glob("*.json")} - {"fixture-manifest.json"}
    if actual_files != EXPECTED_FILES:
        raise ValueError("fixture filesystem set drift")
    for name, expected_hash in manifest["documents"].items():
        value = (bundle / name).read_bytes()
        if sha256_bytes(value) != expected_hash:
            raise ValueError(f"fixture hash drift: {name}")
        if any(marker in value for marker in PRIVATE_KEY_MARKERS):
            raise ValueError(f"private key material detected: {name}")
    contract = _load(bundle / "capability-contract.json")
    suite = _load(bundle / "suite.json")
    request = _load(bundle / "request.json")
    contribution = _load(bundle / "contribution.json")
    result = _load(bundle / "result.json")
    acknowledgement = _load(bundle / "acknowledgement.json")
    public_keys = _load(bundle / "public-keys.json")
    with tempfile.TemporaryDirectory() as temporary:
        registry = FileRegistry.initialize(Path(temporary) / "registry")
        registry.ingest_request(request, contract, public_keys, accepted_at="2026-09-09T08:30:00Z")
        registry.ingest_contribution(
            contribution, suite, public_keys, accepted_at="2026-09-09T08:30:01Z"
        )
        registry.ingest_result(
            result, request, contract, suite, public_keys, accepted_at="2026-09-09T08:30:02Z"
        )
        registry.ingest_acknowledgement(
            acknowledgement, contribution, suite, public_keys, accepted_at="2026-09-09T08:30:03Z"
        )
        audit = registry.audit()
    catalog = compile_request_bound_catalog(
        request,
        contract,
        suite,
        [result],
        [acknowledgement],
        public_keys,
        generation=1,
        compiled_at="2026-09-09T08:31:00Z",
    )
    entries = catalog["catalog"]["entries"]
    if not entries or any(entry["qualified_for_routing"] for entry in entries):
        raise ValueError("single-contributor fixture must remain unqualified for routing")
    return {
        "schema": "universal-benchmark-ci-fixture-verification/v1",
        "result": "verified",
        "accepted_identity_count": audit["accepted_identity_count"],
        "catalog_entry_count": len(entries),
        "qualified_entry_count": 0,
        "contains_private_key": False,
        "authority": "synthetic_test_fixture_verification_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_bundle(args.bundle.resolve(strict=True)), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
