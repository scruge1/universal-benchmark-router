"""Verify the saved output of the installed two-contributor CLI path."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_benchmark_exchange import (  # noqa: E402
    validate_catalog,
    validate_universal_route_decision,
)
from universal_benchmark_registry import sha256_bytes, strict_json_bytes  # noqa: E402


PRIVATE_KEY_MARKERS = (
    b"-----BEGIN " + b"PRIVATE KEY-----",
    b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----",
)


def _load(path: Path) -> object:
    return strict_json_bytes(path.read_bytes())


def verify(bundle: Path, catalog_path: Path, decision_path: Path) -> dict[str, object]:
    manifest = _load(bundle / "fixture-manifest.json")
    if manifest["schema"] != "universal-benchmark-e2e-cli-fixture/v1":
        raise ValueError("fixture manifest schema drift")
    if manifest["authority"] != "synthetic_test_fixture_only_never_routing_evidence":
        raise ValueError("fixture authority drift")
    if manifest["contains_private_key"] is not False or manifest["synthetic_contributor_count"] != 2:
        raise ValueError("fixture safety policy drift")
    for name, digest in manifest["documents"].items():
        payload = (bundle / name).read_bytes()
        if sha256_bytes(payload) != digest:
            raise ValueError(f"fixture hash drift: {name}")
        if any(marker in payload for marker in PRIVATE_KEY_MARKERS):
            raise ValueError(f"private key material detected: {name}")
    suite = _load(bundle / "suite.json")
    catalog = _load(catalog_path)
    decision = _load(decision_path)
    validate_catalog(catalog, suite)
    validate_universal_route_decision(decision)
    if not catalog["entries"] or not all(item["qualified_for_routing"] for item in catalog["entries"]):
        raise ValueError("two-contributor fixture did not qualify")
    if decision["local_decision"]["outcome"] != "selected":
        raise ValueError("installed CLI did not select the eligible fixture route")
    return {
        "schema": "universal-benchmark-e2e-cli-verification/v1",
        "result": "verified",
        "qualified_entry_count": len(catalog["entries"]),
        "selected_route_id": decision["local_decision"]["selected"]["route_id"],
        "contains_private_key": False,
        "authority": "synthetic_installed_cli_verification_only",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args()
    result = verify(
        args.bundle.resolve(strict=True),
        args.catalog.resolve(strict=True),
        args.decision.resolve(strict=True),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
