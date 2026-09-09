from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path

from test_universal_benchmark_exchange import (
    acknowledgement_fixture,
    contract_fixture,
    contribution_fixture,
    public_keys_fixture,
    request_fixture,
    result_fixture,
    sign_document,
)
from test_universal_model_router import suite_fixture
from universal_benchmark_exchange import request_payload
from universal_benchmark_registry import (
    FileRegistry,
    RegistryConflictError,
    RegistryError,
    RegistryReplayError,
    canonical_json_bytes,
    strict_json_bytes,
)
from universal_router_cli import build_parser, run


class UniversalBenchmarkRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "registry"
        self.registry = FileRegistry.initialize(self.root)
        self.contract = contract_fixture()
        self.suite = suite_fixture()
        self.contribution = contribution_fixture(self.suite)
        self.request = request_fixture(self.contract, self.suite, self.contribution)
        self.result = result_fixture(self.request, self.contribution, self.suite)
        self.contribution = self.result["contribution"]
        self.acknowledgement = acknowledgement_fixture(self.contribution, validator_id="validator-a")
        self.public_keys = public_keys_fixture("issuer", "contributor-a", "validator-a")

    def test_full_signed_intake_and_integrity_audit_pass(self) -> None:
        self.registry.ingest_request(self.request, self.contract, self.public_keys, accepted_at="2026-09-09T08:20:00Z")
        self.registry.ingest_contribution(
            self.contribution, self.suite, self.public_keys, accepted_at="2026-09-09T08:20:01Z"
        )
        self.registry.ingest_result(
            self.result, self.request, self.contract, self.suite, self.public_keys,
            accepted_at="2026-09-09T08:20:02Z",
        )
        self.registry.ingest_acknowledgement(
            self.acknowledgement, self.contribution, self.suite, self.public_keys,
            accepted_at="2026-09-09T08:20:03Z",
        )
        self.assertEqual(self.request, self.registry.read("request", self.request["request_id"]))
        self.assertEqual(
            {
                "schema": "universal-benchmark-registry-audit/v1",
                "result": "verified",
                "accepted_identity_count": 4,
                "object_count": 4,
                "event_count": 4,
                "authority": "offline_integrity_verification_only",
            },
            self.registry.audit(),
        )

    def test_replay_and_signed_identity_conflict_fail_closed(self) -> None:
        self.registry.ingest_request(self.request, self.contract, self.public_keys)
        with self.assertRaises(RegistryReplayError):
            self.registry.ingest_request(self.request, self.contract, self.public_keys)
        conflict = copy.deepcopy(self.request)
        conflict["expires_at"] = "2026-11-08T16:00:00Z"
        sign_document(conflict, request_payload(conflict), "issuer")
        with self.assertRaises(RegistryConflictError):
            self.registry.ingest_request(conflict, self.contract, self.public_keys)

    def test_invalid_signature_and_dependency_drift_write_nothing(self) -> None:
        invalid = copy.deepcopy(self.request)
        invalid["issuer"]["issuer_id"] = "tampered"
        with self.assertRaises(ValueError):
            self.registry.ingest_request(invalid, self.contract, self.public_keys)
        wrong_suite = copy.deepcopy(self.suite)
        wrong_suite["suite_id"] = "wrong-suite"
        with self.assertRaises(ValueError):
            self.registry.ingest_result(
                self.result, self.request, self.contract, wrong_suite, self.public_keys
            )
        self.assertEqual(0, self.registry.audit()["accepted_identity_count"])

    def test_result_and_acknowledgement_require_accepted_dependencies(self) -> None:
        with self.assertRaisesRegex(RegistryError, "accepted request dependency"):
            self.registry.ingest_result(
                self.result, self.request, self.contract, self.suite, self.public_keys
            )
        self.registry.ingest_request(self.request, self.contract, self.public_keys)
        with self.assertRaisesRegex(RegistryError, "accepted contribution dependency"):
            self.registry.ingest_result(
                self.result, self.request, self.contract, self.suite, self.public_keys
            )
        with self.assertRaisesRegex(RegistryError, "accepted contribution dependency"):
            self.registry.ingest_acknowledgement(
                self.acknowledgement, self.contribution, self.suite, self.public_keys
            )

    def test_object_and_event_mutation_are_detected(self) -> None:
        claim = self.registry.ingest_request(self.request, self.contract, self.public_keys)
        object_path = self.registry._hash_path("objects", claim["object_sha256"])
        os.chmod(object_path, 0o644)
        object_path.write_bytes(object_path.read_bytes() + b" ")
        with self.assertRaisesRegex(RegistryError, "integrity failure"):
            self.registry.audit()

    def test_duplicate_json_keys_and_uninitialized_root_fail(self) -> None:
        with self.assertRaisesRegex(RegistryError, "duplicate JSON key"):
            strict_json_bytes(b'{"a":1,"a":2}')
        with self.assertRaisesRegex(RegistryError, "initialized registry marker"):
            FileRegistry(Path(self.temporary.name) / "missing")
        with self.assertRaises(RegistryReplayError):
            FileRegistry.initialize(self.root)

    def test_exact_orphan_object_can_be_recovered_but_bad_bytes_cannot(self) -> None:
        document_bytes = canonical_json_bytes(self.request)
        digest = __import__("hashlib").sha256(document_bytes).hexdigest()
        path = self.registry._hash_path("objects", digest)
        self.registry._exclusive_write(path, document_bytes, self.registry.root)
        self.registry.ingest_request(self.request, self.contract, self.public_keys)
        self.assertEqual(1, self.registry.audit()["accepted_identity_count"])

    def test_cli_validate_ingest_and_audit_use_same_library(self) -> None:
        fixture_root = Path(self.temporary.name) / "fixtures"
        fixture_root.mkdir()
        files = {
            "request": self.request,
            "capability-contract": self.contract,
            "public-keys": self.public_keys,
        }
        paths: dict[str, Path] = {}
        for name, value in files.items():
            path = fixture_root / f"{name}.json"
            path.write_bytes(canonical_json_bytes(value))
            paths[name] = path
        parser = build_parser()
        common = [
            "--request", str(paths["request"]),
            "--capability-contract", str(paths["capability-contract"]),
            "--public-keys", str(paths["public-keys"]),
        ]
        self.assertEqual("verified", run(parser.parse_args(["validate-request", *common]))["result"])
        cli_root = Path(self.temporary.name) / "cli-registry"
        self.assertEqual("initialized", run(parser.parse_args(["registry-init", "--root", str(cli_root)]))["result"])
        self.assertEqual(
            "accepted",
            run(parser.parse_args(["ingest-request", *common, "--root", str(cli_root)]))["result"],
        )
        self.assertEqual(
            1,
            run(parser.parse_args(["registry-audit", "--root", str(cli_root)]))["accepted_identity_count"],
        )


if __name__ == "__main__":
    unittest.main()
