from __future__ import annotations

import copy
import hashlib
import json
import unittest
from pathlib import Path

from universal_model_router import (
    RouterContractError,
    canonical_sha256,
    select_route,
    validate_benchmark_profile,
    validate_route_decision,
    validate_task_suite,
    validate_test_configuration,
)


ROOT = Path(__file__).parent
SUITE_PATH = ROOT / "router" / "standard-task-suite-v1.json"
SCHEMA_DIR = ROOT / "router-schemas"
GIB = 1024**3


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def suite_fixture() -> dict:
    return json.loads(SUITE_PATH.read_text(encoding="utf-8"))


def configuration_fixture(suite: dict, *, name: str, gpu_count: int, offload_gib: int = 0) -> dict:
    gpu_uuids = [f"GPU-{index:04d}" for index in range(gpu_count)]
    parallelism = [{"mode": "tensor", "size": gpu_count}] if gpu_count else [{"mode": "cpu_moe", "size": 1}]
    return {
        "schema": "model-test-configuration/v1",
        "configuration_id": f"config-{name}",
        "captured_at": "2026-09-07T22:30:00Z",
        "artifact": {
            "artifact_id": "deepseek-v4-iq4",
            "manifest_sha256": digest("artifact"),
            "repository": "fixture/deepseek-v4",
            "revision": "fixture-revision",
            "files_sha256": digest("files"),
            "tokenizer_sha256": digest("tokenizer"),
            "chat_template_sha256": digest("template"),
            "quantization": "IQ4_NL",
        },
        "engine": {
            "name": "llama.cpp",
            "version": "b10718",
            "build_sha256": digest("engine-build"),
            "runtime_image_sha256": digest("runtime"),
            "argv": ["llama-server", "--split-mode", "tensor", "--tensor-split", ",".join("1" for _ in gpu_uuids)],
            "environment": {"GGML_VK_VISIBLE_DEVICES": ",".join(str(index) for index in range(gpu_count))},
        },
        "hardware": {
            "inventory_sha256": digest("inventory"),
            "host_id": "rig-fixture",
            "boot_id": "boot-fixture",
            "driver_version": "fixture-driver",
            "cuda_version": "fixture-cuda",
            "cpu_model": "EPYC fixture",
            "logical_cpu_count": 64,
            "ram_total_bytes": 256 * GIB,
            "numa_policy": "node-0",
            "gpus": [
                {"uuid": uuid, "pci_bus_id": f"0000:{index + 1:02x}:00.0", "model": "RTX 3090", "vram_bytes": 24 * GIB, "power_limit_watts": 220}
                for index, uuid in enumerate(gpu_uuids)
            ],
            "links_sha256": digest("links"),
            "power_policy_sha256": digest("power-policy"),
        },
        "placement": {
            "gpu_uuid_order": gpu_uuids,
            "parallelism": parallelism,
            "tensor_split": [1 for _ in gpu_uuids],
            "gpu_layers": 44,
            "cpu_moe_layers": 0 if not offload_gib else 16,
            "cpu_offload_bytes": offload_gib * GIB,
            "disk_offload_bytes": 0,
            "kv_cache_dtype": "q8_0",
            "kv_cache_bytes": 8 * GIB,
            "memory_utilization_ratio": 0.9,
            "swap_policy": "forbidden",
        },
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "top_k": 0,
            "seed": 42,
            "max_tokens": 512,
            "reasoning_mode": "enabled",
            "structured_output": "case_defined",
        },
        "workload": {
            "suite_id": suite["suite_id"],
            "suite_sha256": canonical_sha256(suite),
            "case_ids": [case["case_id"] for case in suite["cases"]],
            "workload_id": "throughput-mid-c8",
            "warmup_requests": 8,
            "measured_requests": 96,
            "duration_seconds": 600,
        },
        "background_state": {
            "active_services_sha256": digest("services"),
            "active_processes_sha256": digest("processes"),
            "network_state_sha256": digest("network"),
            "storage_state_sha256": digest("storage"),
            "notes": "synthetic saved fixture",
        },
        "evidence": {
            "capture_manifest_sha256": digest("capture"),
            "runner_sha256": digest("runner"),
            "raw_evidence_sha256": digest(f"evidence-{name}"),
        },
        "authority": {"scope": "saved_test_configuration_only", "may_execute_live": False, "may_publish_route": False},
    }


def profile_fixture(
    suite: dict,
    *,
    name: str,
    gpu_count: int,
    quality: float,
    goodput: float,
    latency_ms: float,
    energy: float,
    axis: str = "vertical",
    offload_gib: int = 0,
) -> dict:
    config = configuration_fixture(suite, name=name, gpu_count=gpu_count, offload_gib=offload_gib)
    return {
        "schema": "model-benchmark-profile/v1",
        "profile_id": f"profile-{name}",
        "configuration": config,
        "configuration_sha256": canonical_sha256(config),
        "suite_id": suite["suite_id"],
        "suite_sha256": canonical_sha256(suite),
        "route": {"route_id": f"route-{name}", "endpoint": f"http://127.0.0.1:{8100 + gpu_count}/v1", "served_model_id": f"model-{name}", "active_manifest_sha256": digest(f"route-manifest-{name}")},
        "capabilities": ["chat", "tools", "structured_output"],
        "trust_zones": ["private"],
        "observed_at": "2026-09-07T22:30:00Z",
        "expires_at": "2026-10-07T22:30:00Z",
        "scaling": {
            "series_id": "deepseek-v4-iq4-tensor-ladder",
            "axis": axis,
            "point_id": f"point-{name}",
            "gpu_count": gpu_count,
            "replica_count": 2 if axis == "horizontal" else 1,
            "parallelism_sha256": canonical_sha256(config["placement"]["parallelism"]),
            "cpu_offload_bytes": offload_gib * GIB,
            "disk_offload_bytes": 0,
            "baseline_point_id": None if gpu_count == 1 else "point-one-gpu",
            "speedup_ratio": goodput / 10,
            "scaling_efficiency_ratio": goodput / (10 * max(gpu_count, 1)),
        },
        "task_scores": [
            {"family": family, "case_ids": [case["case_id"] for case in suite["cases"] if case["family"] == family], "score": quality, "pass_rate": quality, "sample_count": 12, "standard_error": 0.02, "checker_evidence_sha256": digest(f"checker-{name}-{family}")}
            for family in ("niche_coding", "mathematics", "reasoning")
        ],
        "performance": [{
            "workload_id": "throughput-mid-c8",
            "context_tokens": 4096,
            "output_tokens": 256,
            "concurrency": 8,
            "goodput_tokens_per_second": goodput,
            "e2e_p95_ms": latency_ms,
            "ttft_p95_ms": latency_ms / 4,
            "tpot_p95_ms": latency_ms / 100,
            "energy_joules_per_output_token": energy,
            "startup_seconds": 90,
            "completed_requests": 96,
            "failed_requests": 0,
            "thermal_throttle_events": 0,
        }],
        "reliability": {"request_success_ratio": 1.0, "restoration_verified": True, "oom_events": 0, "preemptions": 0},
        "resource_cost": {"gpu_count": gpu_count, "total_vram_bytes": gpu_count * 24 * GIB, "host_ram_bytes": offload_gib * GIB, "cpu_threads": 32 if offload_gib else 8, "power_limit_watts_total": gpu_count * 220},
        "hard_gates_passed": True,
        "evidence_sha256": digest(f"profile-evidence-{name}"),
        "authority": {"scope": "saved_benchmark_profile_only", "may_claim_unmeasured": False, "may_publish_route": False},
    }


def request_fixture(suite: dict) -> dict:
    return {
        "schema": "model-route-request/v1",
        "request_id": "request-fixture",
        "as_of": "2026-09-08T00:00:00Z",
        "suite_id": suite["suite_id"],
        "suite_sha256": canonical_sha256(suite),
        "task_requirements": [
            {"family": family, "minimum_score": 0.7, "minimum_pass_rate": 0.7, "minimum_samples": 10, "weight": 1}
            for family in ("niche_coding", "mathematics", "reasoning")
        ],
        "capabilities": ["chat"],
        "workload_id": "throughput-mid-c8",
        "context_tokens": 4096,
        "output_tokens": 256,
        "concurrency": 8,
        "trust_zone": "private",
        "allowed_scale_axes": ["vertical", "horizontal", "hybrid"],
        "limits": {"max_e2e_p95_ms": 5000, "max_energy_joules_per_output_token": 20, "max_gpu_count": 5, "max_host_ram_bytes": 192 * GIB, "minimum_request_success_ratio": 0.99, "require_restoration": True},
        "objective_order": ["quality_score", "goodput_tokens_per_second", "e2e_p95_ms", "energy_joules_per_output_token", "gpu_count"],
        "authority": {"scope": "offline_route_selection_only", "may_contact_endpoint": False, "may_mutate_services": False},
    }


class UniversalModelRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.suite = suite_fixture()

    def test_router_schema_documents_parse_and_have_unique_ids(self) -> None:
        documents = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SCHEMA_DIR.glob("*.schema.json"))]
        self.assertEqual(8, len(documents))
        self.assertEqual(8, len({item["$id"] for item in documents}))
        self.assertTrue(all(item["$schema"].endswith("2020-12/schema") for item in documents))

    def test_standard_suite_hashes_and_required_families_pass(self) -> None:
        validate_task_suite(self.suite)

    def test_prompt_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.suite)
        changed["cases"][0]["prompt"] += " Changed."
        with self.assertRaisesRegex(RouterContractError, "prompt hash drift"):
            validate_task_suite(changed)

    def test_complete_configuration_and_profile_pass(self) -> None:
        profile = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        validate_test_configuration(profile["configuration"])
        validate_benchmark_profile(profile, self.suite)

    def test_gpu_order_drift_fails_closed(self) -> None:
        config = configuration_fixture(self.suite, name="tp4", gpu_count=4)
        config["placement"]["gpu_uuid_order"].reverse()
        with self.assertRaisesRegex(RouterContractError, "GPU order"):
            validate_test_configuration(config)

    def test_configuration_hash_drift_fails_closed(self) -> None:
        profile = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        profile["configuration"]["engine"]["argv"].append("--changed")
        with self.assertRaisesRegex(RouterContractError, "configuration hash drift"):
            validate_benchmark_profile(profile, self.suite)

    def test_quality_first_selects_stronger_vertical_point(self) -> None:
        tp2 = profile_fixture(self.suite, name="tp2", gpu_count=2, quality=0.76, goodput=45, latency_ms=1400, energy=5)
        tp4 = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.92, goodput=78, latency_ms=850, energy=4)
        decision = select_route(request_fixture(self.suite), [tp2, tp4], self.suite)
        self.assertEqual("route-tp4", decision["selected"]["route_id"])
        self.assertEqual("vertical", decision["candidates"][0]["scale_axis"])

    def test_resource_first_selects_smaller_vertical_point(self) -> None:
        tp2 = profile_fixture(self.suite, name="tp2", gpu_count=2, quality=0.82, goodput=45, latency_ms=1400, energy=5)
        tp4 = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=78, latency_ms=850, energy=4)
        request = request_fixture(self.suite)
        request["objective_order"] = ["gpu_count", "host_ram_bytes", "quality_score"]
        decision = select_route(request, [tp4, tp2], self.suite)
        self.assertEqual("route-tp2", decision["selected"]["route_id"])

    def test_vertical_can_win_over_horizontal_replica(self) -> None:
        vertical = profile_fixture(self.suite, name="vertical4", gpu_count=4, quality=0.9, goodput=90, latency_ms=800, energy=4, axis="vertical")
        horizontal = profile_fixture(self.suite, name="replica2", gpu_count=4, quality=0.9, goodput=82, latency_ms=950, energy=5, axis="horizontal")
        request = request_fixture(self.suite)
        request["objective_order"] = ["goodput_tokens_per_second", "e2e_p95_ms"]
        decision = select_route(request, [horizontal, vertical], self.suite)
        self.assertEqual("route-vertical4", decision["selected"]["route_id"])

    def test_cpu_offload_point_is_eligible_when_within_ram_limit(self) -> None:
        offload = profile_fixture(self.suite, name="offload2", gpu_count=2, quality=0.8, goodput=20, latency_ms=3000, energy=9, offload_gib=96)
        decision = select_route(request_fixture(self.suite), [offload], self.suite)
        self.assertEqual("selected", decision["outcome"])

    def test_stale_profile_returns_auditable_no_route(self) -> None:
        profile = profile_fixture(self.suite, name="stale", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        profile["expires_at"] = "2026-09-07T23:00:00Z"
        decision = select_route(request_fixture(self.suite), [profile], self.suite)
        self.assertEqual("no_eligible_route", decision["outcome"])
        self.assertIn("stale_benchmark_evidence", decision["candidates"][0]["exclusion_reasons"])

    def test_unmeasured_workload_fails_closed(self) -> None:
        profile = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        request = request_fixture(self.suite)
        request["workload_id"] = "long-context-c2"
        decision = select_route(request, [profile], self.suite)
        self.assertIn("workload_not_measured", decision["candidates"][0]["exclusion_reasons"])

    def test_task_floor_blocks_fast_but_weak_profile(self) -> None:
        profile = profile_fixture(self.suite, name="fast-weak", gpu_count=2, quality=0.6, goodput=120, latency_ms=500, energy=2)
        decision = select_route(request_fixture(self.suite), [profile], self.suite)
        self.assertEqual("no_eligible_route", decision["outcome"])
        self.assertTrue(any(reason.startswith("task_score_below_floor") for reason in decision["candidates"][0]["exclusion_reasons"]))

    def test_context_and_concurrency_envelope_fail_closed(self) -> None:
        profile = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        request = request_fixture(self.suite)
        request["context_tokens"] = 8192
        request["concurrency"] = 16
        reasons = select_route(request, [profile], self.suite)["candidates"][0]["exclusion_reasons"]
        self.assertIn("context_limit_exceeded", reasons)
        self.assertIn("concurrency_limit_exceeded", reasons)

    def test_duplicate_route_identity_is_rejected(self) -> None:
        first = profile_fixture(self.suite, name="first", gpu_count=2, quality=0.8, goodput=40, latency_ms=1500, energy=5)
        second = profile_fixture(self.suite, name="second", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        second["route"]["route_id"] = first["route"]["route_id"]
        with self.assertRaisesRegex(RouterContractError, "duplicate active route identity"):
            select_route(request_fixture(self.suite), [first, second], self.suite)

    def test_selector_is_deterministic_across_input_order(self) -> None:
        first = profile_fixture(self.suite, name="first", gpu_count=2, quality=0.8, goodput=40, latency_ms=1500, energy=5)
        second = profile_fixture(self.suite, name="second", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        left = select_route(request_fixture(self.suite), [first, second], self.suite)
        right = select_route(request_fixture(self.suite), [second, first], self.suite)
        self.assertEqual(left, right)

    def test_decision_contract_rejects_winner_rewrite(self) -> None:
        profile = profile_fixture(self.suite, name="tp4", gpu_count=4, quality=0.9, goodput=80, latency_ms=900, energy=4)
        decision = select_route(request_fixture(self.suite), [profile], self.suite)
        decision["selected"]["route_id"] = "route-rewritten"
        with self.assertRaisesRegex(RouterContractError, "eligible rank 1"):
            validate_route_decision(decision)


if __name__ == "__main__":
    unittest.main()
