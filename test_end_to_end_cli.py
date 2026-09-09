from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from test_universal_benchmark_exchange import (
    acknowledgement_fixture,
    contribution_fixture,
    public_keys_fixture,
)
from test_universal_model_router import profile_fixture, request_fixture, suite_fixture
from universal_benchmark_registry import FileRegistry, RegistryError, canonical_json_bytes, strict_json_bytes
from universal_router_cli import build_parser, run


class EndToEndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry_root = self.root / "registry"
        self.registry = FileRegistry.initialize(self.registry_root)
        self.suite = suite_fixture()
        self.suite_path = self._write("suite.json", self.suite)
        self.public_keys = public_keys_fixture(
            "contributor-a", "contributor-b", "validator-a", "validator-b"
        )
        self.parser = build_parser()

    def _write(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_bytes(canonical_json_bytes(value))
        return path

    def _ingest(self, contributor_id: str, site_id: str, validator_id: str) -> None:
        contribution = contribution_fixture(
            self.suite, contributor_id=contributor_id, site_id=site_id
        )
        acknowledgement = acknowledgement_fixture(contribution, validator_id=validator_id)
        self.registry.ingest_contribution(contribution, self.suite, self.public_keys)
        self.registry.ingest_acknowledgement(
            acknowledgement, contribution, self.suite, self.public_keys
        )

    def _compile(self, name: str = "catalog.json") -> tuple[dict, Path]:
        output = self.root / name
        result = run(
            self.parser.parse_args(
                [
                    "compile-catalog",
                    "--root",
                    str(self.registry_root),
                    "--suite",
                    str(self.suite_path),
                    "--generation",
                    "1",
                    "--compiled-at",
                    "2026-09-09T09:20:00Z",
                    "--output",
                    str(output),
                ]
            )
        )
        return result, output

    def test_export_contracts_matches_repository_assets(self) -> None:
        output = self.root / "contracts"
        result = run(
            self.parser.parse_args(
                ["export-contracts", "--output-dir", str(output)]
            )
        )
        self.assertEqual("exported", result["result"])
        self.assertEqual(2, result["file_count"])
        repository = Path(__file__).parent / "router"
        for name in (
            "benchmark-capability-contract-v2.json",
            "standard-task-suite-v1.json",
        ):
            self.assertEqual((repository / name).read_bytes(), (output / name).read_bytes())

    def test_two_accepted_contributors_compile_and_select(self) -> None:
        self._ingest("contributor-a", "site-a", "validator-b")
        self._ingest("contributor-b", "site-b", "validator-a")
        compiled, catalog_path = self._compile()
        self.assertEqual("saved", compiled["result"])
        catalog = strict_json_bytes(catalog_path.read_bytes())
        self.assertTrue(catalog["entries"])
        self.assertTrue(all(item["qualified_for_routing"] for item in catalog["entries"]))

        route_request = request_fixture(self.suite)
        local_profile = profile_fixture(
            self.suite,
            name="exchange",
            gpu_count=4,
            quality=0.1,
            goodput=40,
            latency_ms=900,
            energy=5,
        )
        request_path = self._write("route-request.json", route_request)
        profile_path = self._write("local-profile.json", local_profile)
        decision_path = self.root / "decision.json"
        routed = run(
            self.parser.parse_args(
                [
                    "route",
                    "--request",
                    str(request_path),
                    "--suite",
                    str(self.suite_path),
                    "--catalog",
                    str(catalog_path),
                    "--profile",
                    str(profile_path),
                    "--output",
                    str(decision_path),
                ]
            )
        )
        self.assertEqual("saved", routed["result"])
        decision = strict_json_bytes(decision_path.read_bytes())
        self.assertEqual("selected", decision["local_decision"]["outcome"])
        self.assertEqual("route-exchange", decision["local_decision"]["selected"]["route_id"])

    def test_one_contributor_compiles_but_cannot_select(self) -> None:
        self._ingest("contributor-a", "site-a", "validator-b")
        _, catalog_path = self._compile()
        catalog = strict_json_bytes(catalog_path.read_bytes())
        self.assertTrue(catalog["entries"])
        self.assertFalse(any(item["qualified_for_routing"] for item in catalog["entries"]))

        request_path = self._write("route-request.json", request_fixture(self.suite))
        profile_path = self._write(
            "local-profile.json",
            profile_fixture(
                self.suite,
                name="exchange",
                gpu_count=4,
                quality=0.9,
                goodput=40,
                latency_ms=900,
                energy=5,
            ),
        )
        decision = run(
            self.parser.parse_args(
                [
                    "route",
                    "--request",
                    str(request_path),
                    "--suite",
                    str(self.suite_path),
                    "--catalog",
                    str(catalog_path),
                    "--profile",
                    str(profile_path),
                ]
            )
        )
        self.assertEqual("no_eligible_route", decision["local_decision"]["outcome"])
        self.assertTrue(
            all(
                reason.startswith("task_family_unmeasured:")
                for reason in decision["local_decision"]["candidates"][0]["exclusion_reasons"]
            )
        )

    def test_unaccepted_file_is_ignored_and_output_is_absent_only(self) -> None:
        unaccepted = contribution_fixture(self.suite)
        self._write("unaccepted-contribution.json", unaccepted)
        first, _ = self._compile()
        self.assertEqual("saved", first["result"])
        with self.assertRaisesRegex(RegistryError, "output must be absent"):
            self._compile()
        catalog = strict_json_bytes((self.root / "catalog.json").read_bytes())
        self.assertEqual([], catalog["entries"])

    def test_registry_snapshot_rejects_unknown_type_and_detects_mutation(self) -> None:
        with self.assertRaisesRegex(RegistryError, "unsupported document type"):
            self.registry.accepted_documents("profile")
        self._ingest("contributor-a", "site-a", "validator-b")
        contribution = self.registry.accepted_documents("contribution")[0]
        claim = self.registry._existing_claim("contribution", contribution["submission_id"])
        object_path = self.registry._hash_path("objects", claim["object_sha256"])
        object_path.chmod(0o644)
        object_path.write_bytes(object_path.read_bytes() + b" ")
        with self.assertRaisesRegex(RegistryError, "integrity failure"):
            self._compile("mutated-catalog.json")

    def test_generation_and_malformed_route_input_fail_closed(self) -> None:
        with self.assertRaisesRegex(RegistryError, "positive integer"):
            run(
                self.parser.parse_args(
                    [
                        "compile-catalog",
                        "--root",
                        str(self.registry_root),
                        "--suite",
                        str(self.suite_path),
                        "--generation",
                        "0",
                        "--compiled-at",
                        "2026-09-09T09:20:00Z",
                    ]
                )
            )
        malformed = self.root / "malformed.json"
        malformed.write_text('{"schema":1,"schema":2}', encoding="utf-8")
        _, catalog_path = self._compile("valid-catalog.json")
        profile_path = self._write(
            "local-profile.json",
            profile_fixture(
                self.suite,
                name="exchange",
                gpu_count=4,
                quality=0.9,
                goodput=40,
                latency_ms=900,
                energy=5,
            ),
        )
        with self.assertRaisesRegex(RegistryError, "duplicate JSON key"):
            run(
                self.parser.parse_args(
                    [
                        "route",
                        "--request",
                        str(malformed),
                        "--suite",
                        str(self.suite_path),
                        "--catalog",
                        str(catalog_path),
                        "--profile",
                        str(profile_path),
                    ]
                )
            )


if __name__ == "__main__":
    unittest.main()
