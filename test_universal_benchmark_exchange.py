from __future__ import annotations

import copy
import base64
import hashlib
import json
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from test_universal_model_router import profile_fixture, request_fixture as route_request_fixture, suite_fixture
from universal_benchmark_exchange import (
    ACK_SCHEMA,
    CONTRIBUTION_SCHEMA,
    REQUEST_SCHEMA,
    RESULT_ENVELOPE_SCHEMA,
    ExchangeContractError,
    acknowledgement_payload,
    compile_catalog,
    compile_request_bound_catalog,
    contribution_payload,
    evaluation_protocol,
    model_variant,
    request_payload,
    result_envelope_payload,
    select_route_with_request_bound_evidence,
    validate_benchmark_request,
    validate_contribution,
    validate_result_envelope,
    validate_request_bound_catalog,
    validate_request_bound_route_decision,
)
from universal_model_router import canonical_sha256


ROOT = Path(__file__).parent
CONTRACT_PATH = ROOT / "router" / "benchmark-capability-contract-v2.json"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def key_material(label: str) -> tuple[Ed25519PrivateKey, str, str]:
    private_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"private-{label}".encode("utf-8")).digest())
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return private_key, hashlib.sha256(public_key).hexdigest(), base64.b64encode(public_key).decode("ascii")


def sign_document(document: dict, payload: dict, label: str) -> None:
    private_key, key_id, _ = key_material(label)
    document["attestation"]["key_id"] = key_id
    document["attestation"]["payload_sha256"] = canonical_sha256(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    document["attestation"]["signature"] = base64.b64encode(private_key.sign(canonical)).decode("ascii")


def public_keys_fixture(*labels: str) -> dict[str, str]:
    return {key_id: encoded for label in labels for _, key_id, encoded in [key_material(label)]}


def contract_fixture() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def contribution_fixture(suite: dict, *, contributor_id: str = "contributor-a", site_id: str = "site-a") -> dict:
    profile = profile_fixture(suite, name="exchange", gpu_count=4, quality=0.9, goodput=40, latency_ms=900, energy=5)
    variant = model_variant(profile)
    protocol = evaluation_protocol(profile)
    records = []
    for repetition_index, seed in enumerate((11, 22, 33)):
        for case in suite["cases"]:
            records.append({
                "case_id": case["case_id"],
                "family": case["family"],
                "trial_id": f"trial-{repetition_index}",
                "repetition_index": repetition_index,
                "seed": seed,
                "score": 1.0,
                "passed": True,
                "attempt_count": 1,
                "prompt_sha256": case["prompt_sha256"],
                "checker_specification_sha256": case["checker"]["specification_sha256"],
                "output_sha256": digest(f"output-{contributor_id}-{case['case_id']}-{seed}"),
                "checker_receipt_sha256": digest(f"receipt-{contributor_id}-{case['case_id']}-{seed}"),
                "measurements": {
                    "quality": {
                        "correctness": 1.0,
                        "instruction_adherence": 1.0,
                        "groundedness": 1.0,
                        "tool_execution": None,
                        "failure_recovery": None,
                        "safety": 1.0,
                    },
                    "unsupported_claim_count": 0,
                    "tool_call_count": 0,
                    "tool_failure_count": 0,
                    "recovered_tool_failure_count": 0,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "reasoning_tokens": 10,
                    "wall_time_ms": 500,
                    "grader_kind": "deterministic",
                    "grader_identity_sha256": digest("grader"),
                },
            })
    contribution = {
        "schema": CONTRIBUTION_SCHEMA,
        "submission_id": f"submission-{contributor_id}",
        "observed_at": "2026-09-08T16:10:00Z",
        "contributor": {"contributor_id": contributor_id, "public_key_id": key_material(contributor_id)[1]},
        "site": {"site_id": site_id, "system_class_sha256": digest(f"system-{site_id}"), "hardware_inventory_sha256": digest(f"hardware-{site_id}")},
        "suite_id": suite["suite_id"],
        "suite_sha256": canonical_sha256(suite),
        "model_variant": variant,
        "model_variant_sha256": canonical_sha256(variant),
        "evaluation_protocol": protocol,
        "evaluation_protocol_sha256": canonical_sha256(protocol),
        "configuration_id": profile["configuration"]["configuration_id"],
        "configuration_sha256": profile["configuration_sha256"],
        "task_records": records,
        "system_result": {
            "workload_id": "throughput-mid-c8",
            "goodput_tokens_per_second": 40,
            "e2e_p95_ms": 900,
            "energy_joules_per_output_token": 5,
            "request_success_ratio": 1.0,
            "performance_evidence_sha256": digest(f"performance-{site_id}"),
        },
        "evidence": {"raw_manifest_sha256": digest("raw"), "runner_sha256": digest("runner"), "configuration_capture_sha256": digest(f"config-{site_id}")},
        "disclosure": {"license": "CC-BY-4.0", "redistribution_allowed": True, "contains_private_prompts": False, "contains_personal_data": False},
        "attestation": {"algorithm": "ed25519", "key_id": key_material(contributor_id)[1], "payload_sha256": "", "signature": ""},
        "authority": {"scope": "saved_untrusted_contribution_only", "may_enter_catalog": False},
    }
    sign_document(contribution, contribution_payload(contribution), contributor_id)
    return contribution


def request_fixture(contract: dict, suite: dict, contribution: dict) -> dict:
    lane = next(item for item in contract["capability_lanes"] if item["lane_id"] == "single_turn_core")
    request = {
        "schema": REQUEST_SCHEMA,
        "request_id": "request-single-turn-core-1",
        "issued_at": "2026-09-08T16:00:00Z",
        "expires_at": "2026-10-08T16:00:00Z",
        "issuer": {"issuer_id": "benchmark-catalog", "public_key_id": key_material("issuer")[1]},
        "capability_contract": {"contract_id": contract["contract_id"], "version": contract["version"], "sha256": canonical_sha256(contract)},
        "lane": {key: lane[key] for key in ("lane_id", "dimension", "evidence_scope", "tier", "claim_cap", "benchmark_source_ids", "required_subtests", "objective_checkers")},
        "task_pack": {"pack_id": suite["suite_id"], "revision": "fixture-v1", "manifest_sha256": canonical_sha256(suite), "visibility": "public_pinned", "redistribution_allowed": True, "cross_site_allowed": True, "case_ids": [case["case_id"] for case in suite["cases"]]},
        "test_cells": [{
            "cell_id": "cell-exchange",
            "model_variant_sha256": contribution["model_variant_sha256"],
            "fine_tune_id": contribution["model_variant"]["revision"],
            "quantization": contribution["model_variant"]["quantization"],
            "context_tokens": 4096,
            "temperature": contribution["evaluation_protocol"]["generation"]["temperature"],
            "top_p": contribution["evaluation_protocol"]["generation"]["top_p"],
            "engine_name": contribution["evaluation_protocol"]["engine_name"],
            "evaluation_protocol_sha256": contribution["evaluation_protocol_sha256"],
            "system_constraints": {
                "system_class_sha256": None,
                "gpu_count": None,
                "gpu_model_set_sha256": None,
                "gpu_uuid_order_sha256": None,
                "parallelism_sha256": None,
                "cpu_offload_bytes": None,
                "power_limit_watts_total": None,
            },
        }],
        "comparison_axis": "cross_site_evidence",
        "trial_policy": {"minimum_trials": 3, "maximum_trials": 3, "seeds": [11, 22, 33], "one_axis_change_per_comparison": True},
        "required_metrics": contract["required_metrics"],
        "contribution_policy": {"minimum_distinct_contributors": 2, "minimum_independent_validators_per_submission": 1, "signature_algorithm": "ed25519", "retain_site_identity": True, "system_performance_pooling": "forbidden"},
        "attestation": {"algorithm": "ed25519", "key_id": key_material("issuer")[1], "payload_sha256": "", "signature": ""},
        "authority": {"scope": "saved_benchmark_request_only", "may_execute_live": False, "may_accept_submission": False, "may_publish": False},
    }
    sign_document(request, request_payload(request), "issuer")
    return request


def result_fixture(request: dict, contribution: dict, suite: dict) -> dict:
    configuration = profile_fixture(suite, name="exchange", gpu_count=4, quality=0.9, goodput=40, latency_ms=900, energy=5)["configuration"]
    configuration["hardware"]["inventory_sha256"] = contribution["site"]["hardware_inventory_sha256"]
    contribution["configuration_sha256"] = canonical_sha256(configuration)
    contribution["attestation"]["payload_sha256"] = canonical_sha256(contribution_payload(contribution))
    sign_document(contribution, contribution_payload(contribution), contribution["contributor"]["contributor_id"])
    result = {
        "schema": RESULT_ENVELOPE_SCHEMA,
        "result_id": f"result-{contribution['submission_id']}",
        "request_id": request["request_id"],
        "request_sha256": canonical_sha256(request),
        "cell_id": request["test_cells"][0]["cell_id"],
        "observed_context_tokens": request["test_cells"][0]["context_tokens"],
        "configuration": configuration,
        "contribution": contribution,
        "attestation": {"algorithm": "ed25519", "key_id": contribution["contributor"]["public_key_id"], "payload_sha256": "", "signature": ""},
        "authority": {"scope": "saved_request_bound_result_only", "may_enter_catalog": False, "may_publish": False},
    }
    sign_document(result, result_envelope_payload(result), contribution["contributor"]["contributor_id"])
    return result


def acknowledgement_fixture(contribution: dict, *, validator_id: str) -> dict:
    ack = {
        "schema": ACK_SCHEMA,
        "acknowledgement_id": f"ack-{contribution['submission_id']}",
        "acknowledged_at": "2026-09-08T16:20:00Z",
        "submission_id": contribution["submission_id"],
        "submission_sha256": canonical_sha256(contribution),
        "validator": {"validator_id": validator_id, "public_key_id": key_material(validator_id)[1]},
        "verdict": "accepted",
        "checks": {"signature_verified": True, "suite_verified": True, "variant_verified": True, "protocol_verified": True, "configuration_verified": True, "raw_evidence_verified": True, "system_class_verified": True, "replay_absent": True},
        "reasons": [],
        "evidence_sha256": digest(f"ack-evidence-{validator_id}"),
        "attestation": {"algorithm": "ed25519", "key_id": key_material(validator_id)[1], "payload_sha256": "", "signature": ""},
        "authority": {"scope": "saved_acknowledgement_only", "may_publish_catalog": False},
    }
    sign_document(ack, acknowledgement_payload(ack), validator_id)
    return ack


class UniversalBenchmarkExchangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = contract_fixture()
        self.suite = suite_fixture()
        self.contribution = contribution_fixture(self.suite)
        self.request = request_fixture(self.contract, self.suite, self.contribution)
        self.result = result_fixture(self.request, self.contribution, self.suite)
        self.public_keys = public_keys_fixture("issuer", "contributor-a", "contributor-b", "validator-a", "validator-b")

    def test_standard_request_and_request_bound_result_pass(self) -> None:
        validate_benchmark_request(self.request, self.contract)
        validate_result_envelope(self.result, self.request, self.contract, self.suite)

    def test_repeated_trials_pass_contribution_validation(self) -> None:
        validate_contribution(self.contribution, self.suite)
        self.assertEqual(3 * len(self.suite["cases"]), len(self.contribution["task_records"]))

    def test_duplicate_case_trial_repetition_fails_closed(self) -> None:
        changed = copy.deepcopy(self.contribution)
        changed["task_records"].append(copy.deepcopy(changed["task_records"][0]))
        changed["attestation"]["payload_sha256"] = canonical_sha256(contribution_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "duplicate case, trial"):
            validate_contribution(changed, self.suite)

    def test_capability_contract_hash_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.request)
        changed["capability_contract"]["sha256"] = digest("wrong")
        changed["attestation"]["payload_sha256"] = canonical_sha256(request_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "identity drift"):
            validate_benchmark_request(changed, self.contract)

    def test_lane_semantic_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.request)
        changed["lane"]["claim_cap"] = "global_model_rank"
        changed["attestation"]["payload_sha256"] = canonical_sha256(request_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "lane"):
            validate_benchmark_request(changed, self.contract)

    def test_multiple_cells_cannot_change_more_than_declared_axis(self) -> None:
        changed = copy.deepcopy(self.request)
        second = copy.deepcopy(changed["test_cells"][0])
        second["cell_id"] = "cell-two"
        second["context_tokens"] = 8192
        changed["test_cells"].append(second)
        changed["comparison_axis"] = "temperature"
        changed["attestation"]["payload_sha256"] = canonical_sha256(request_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "more than the declared comparison axis"):
            validate_benchmark_request(changed, self.contract)

    def test_private_pack_cannot_authorize_cross_site_execution(self) -> None:
        changed = copy.deepcopy(self.request)
        changed["task_pack"]["visibility"] = "private_sealed_local"
        changed["task_pack"]["cross_site_allowed"] = True
        changed["attestation"]["payload_sha256"] = canonical_sha256(request_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "sealed local"):
            validate_benchmark_request(changed, self.contract)

    def test_result_request_hash_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.result)
        changed["request_sha256"] = digest("wrong-request")
        changed["attestation"]["payload_sha256"] = canonical_sha256(result_envelope_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "request binding drift"):
            validate_result_envelope(changed, self.request, self.contract, self.suite)

    def test_result_variant_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.result)
        changed["contribution"]["model_variant_sha256"] = digest("other-variant")
        changed["contribution"]["attestation"]["payload_sha256"] = canonical_sha256(contribution_payload(changed["contribution"]))
        changed["attestation"]["payload_sha256"] = canonical_sha256(result_envelope_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "model variant drift|request cell drift"):
            validate_result_envelope(changed, self.request, self.contract, self.suite)

    def test_result_context_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.result)
        changed["observed_context_tokens"] = 8192
        changed["attestation"]["payload_sha256"] = canonical_sha256(result_envelope_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "observed_context_tokens"):
            validate_result_envelope(changed, self.request, self.contract, self.suite)

    def test_result_system_constraint_drift_fails_closed(self) -> None:
        request = copy.deepcopy(self.request)
        request["test_cells"][0]["system_constraints"]["gpu_count"] = 5
        sign_document(request, request_payload(request), "issuer")
        result = result_fixture(request, contribution_fixture(self.suite), self.suite)
        with self.assertRaisesRegex(ExchangeContractError, "system constraint drift for gpu_count"):
            validate_result_envelope(result, request, self.contract, self.suite)

    def test_result_with_too_few_trials_fails_closed(self) -> None:
        changed = copy.deepcopy(self.result)
        changed["contribution"]["task_records"] = [record for record in changed["contribution"]["task_records"] if record["trial_id"] != "trial-2"]
        changed["contribution"]["attestation"]["payload_sha256"] = canonical_sha256(contribution_payload(changed["contribution"]))
        changed["attestation"]["payload_sha256"] = canonical_sha256(result_envelope_payload(changed))
        with self.assertRaisesRegex(ExchangeContractError, "trial count outside"):
            validate_result_envelope(changed, self.request, self.contract, self.suite)

    def test_catalog_aggregates_quality_but_keeps_site_performance_separate(self) -> None:
        second = contribution_fixture(self.suite, contributor_id="contributor-b", site_id="site-b")
        acknowledgements = [
            acknowledgement_fixture(self.contribution, validator_id="validator-b"),
            acknowledgement_fixture(second, validator_id="validator-a"),
        ]
        catalog = compile_catalog(self.suite, [self.contribution, second], acknowledgements, generation=1, compiled_at="2026-09-08T16:30:00Z")
        self.assertTrue(all(entry["qualified_for_routing"] for entry in catalog["entries"]))
        self.assertEqual(2, len(catalog["system_profiles"]))
        self.assertEqual({"site-a", "site-b"}, {profile["site_id"] for profile in catalog["system_profiles"]})
        self.assertTrue(all(entry["measurement_summaries"]["correctness"]["sample_count"] > 0 for entry in catalog["entries"]))

    def test_request_bound_catalog_accepts_two_independent_sites(self) -> None:
        second = contribution_fixture(self.suite, contributor_id="contributor-b", site_id="site-b")
        second_result = result_fixture(self.request, second, self.suite)
        acknowledgements = [
            acknowledgement_fixture(self.contribution, validator_id="validator-b"),
            acknowledgement_fixture(second, validator_id="validator-a"),
        ]
        wrapper = compile_request_bound_catalog(
            self.request,
            self.contract,
            self.suite,
            [self.result, second_result],
            acknowledgements,
            self.public_keys,
            generation=1,
            compiled_at="2026-09-08T16:30:00Z",
        )
        validate_request_bound_catalog(wrapper, self.request, self.contract, self.suite)
        self.assertEqual(2, wrapper["result_count"])
        self.assertTrue(all(entry["qualified_for_routing"] for entry in wrapper["catalog"]["entries"]))

    def test_route_candidate_consumes_request_bound_catalog(self) -> None:
        second = contribution_fixture(self.suite, contributor_id="contributor-b", site_id="site-b")
        wrapper = compile_request_bound_catalog(
            self.request,
            self.contract,
            self.suite,
            [self.result, result_fixture(self.request, second, self.suite)],
            [
                acknowledgement_fixture(self.contribution, validator_id="validator-b"),
                acknowledgement_fixture(second, validator_id="validator-a"),
            ],
            self.public_keys,
            generation=1,
            compiled_at="2026-09-08T16:30:00Z",
        )
        local_profile = profile_fixture(self.suite, name="exchange", gpu_count=4, quality=0.1, goodput=40, latency_ms=900, energy=5)
        decision = select_route_with_request_bound_evidence(
            route_request_fixture(self.suite),
            [local_profile],
            self.suite,
            wrapper,
            self.request,
            self.contract,
        )
        validate_request_bound_route_decision(decision, wrapper, self.request)
        self.assertEqual(wrapper["catalog_id"], decision["request_catalog_id"])
        self.assertEqual("selected", decision["route_decision"]["local_decision"]["outcome"])
        changed = copy.deepcopy(decision)
        changed["benchmark_request_sha256"] = digest("rewritten-request")
        with self.assertRaisesRegex(ExchangeContractError, "benchmark request binding drift"):
            validate_request_bound_route_decision(changed, wrapper, self.request)

    def test_request_bound_catalog_rejects_bare_legacy_contribution(self) -> None:
        with self.assertRaisesRegex(ExchangeContractError, "exact keys|required"):
            compile_request_bound_catalog(
                self.request,
                self.contract,
                self.suite,
                [self.contribution],
                [],
                self.public_keys,
                generation=1,
                compiled_at="2026-09-08T16:30:00Z",
            )

    def test_request_bound_catalog_rejects_replayed_result(self) -> None:
        with self.assertRaisesRegex(ExchangeContractError, "duplicate result identity|replayed result"):
            compile_request_bound_catalog(
                self.request,
                self.contract,
                self.suite,
                [self.result, copy.deepcopy(self.result)],
                [],
                self.public_keys,
                generation=1,
                compiled_at="2026-09-08T16:30:00Z",
            )

    def test_request_bound_catalog_rejects_invalid_ed25519_signature(self) -> None:
        second = contribution_fixture(self.suite, contributor_id="contributor-b", site_id="site-b")
        second_result = result_fixture(self.request, second, self.suite)
        second_result["attestation"]["signature"] = base64.b64encode(b"not-a-valid-signature").decode("ascii")
        acknowledgements = [
            acknowledgement_fixture(self.contribution, validator_id="validator-b"),
            acknowledgement_fixture(second, validator_id="validator-a"),
        ]
        with self.assertRaisesRegex(ExchangeContractError, "Ed25519 signature verification failed"):
            compile_request_bound_catalog(
                self.request,
                self.contract,
                self.suite,
                [self.result, second_result],
                acknowledgements,
                self.public_keys,
                generation=1,
                compiled_at="2026-09-08T16:30:00Z",
            )


if __name__ == "__main__":
    unittest.main()
