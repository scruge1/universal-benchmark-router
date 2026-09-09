"""Offline evidence-based model router for PRD-156 H7-P.

The selector consumes saved benchmark profiles. It does not inspect a host,
call an endpoint, start a model, publish a route, or mutate a service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SUITE_SCHEMA = "model-task-suite/v1"
CONFIG_SCHEMA = "model-test-configuration/v1"
PROFILE_SCHEMA = "model-benchmark-profile/v1"
REQUEST_SCHEMA = "model-route-request/v1"
DECISION_SCHEMA = "model-route-decision/v1"

TASK_FAMILIES = {
    "chat",
    "instruction_following",
    "coding",
    "niche_coding",
    "mathematics",
    "reasoning",
    "structured_output",
    "tool_use",
    "tool_recovery",
    "long_context",
    "safety",
}
SCALE_AXES = {"vertical", "horizontal", "hybrid"}
OBJECTIVES = {
    "quality_score": "max",
    "goodput_tokens_per_second": "max",
    "e2e_p95_ms": "min",
    "energy_joules_per_output_token": "min",
    "gpu_count": "min",
    "host_ram_bytes": "min",
    "startup_seconds": "min",
}


class RouterContractError(ValueError):
    """Saved router input is malformed or internally inconsistent."""


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fail(message: str) -> None:
    raise RouterContractError(message)


def _exact(value: Any, keys: set[str], path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{path}: object is required")
    missing = sorted(keys - set(value))
    extra = sorted(set(value) - keys)
    if missing or extra:
        _fail(f"{path}: exact keys required; missing={missing!r} extra={extra!r}")
    return value


def _sha(value: Any, path: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        _fail(f"{path}: lowercase SHA-256 is required")


def _time(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        _fail(f"{path}: RFC3339 timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RouterContractError(f"{path}: invalid timestamp") from exc
    if parsed.tzinfo is None:
        _fail(f"{path}: timezone is required")
    return parsed.astimezone(timezone.utc)


def validate_task_suite(suite: Mapping[str, Any]) -> None:
    _exact(
        suite,
        {"schema", "suite_id", "version", "created_at", "sources", "cases", "workloads", "authority"},
        "$suite",
    )
    if suite["schema"] != SUITE_SCHEMA:
        _fail(f"$suite.schema: must equal {SUITE_SCHEMA!r}")
    _time(suite["created_at"], "$suite.created_at")
    if not suite["cases"] or not suite["workloads"]:
        _fail("$suite: cases and workloads must not be empty")
    case_ids: set[str] = set()
    families: set[str] = set()
    for index, case in enumerate(suite["cases"]):
        path = f"$suite.cases[{index}]"
        _exact(
            case,
            {"case_id", "family", "skill_tags", "prompt", "prompt_sha256", "checker", "max_output_tokens"},
            path,
        )
        if case["case_id"] in case_ids:
            _fail(f"{path}.case_id: duplicate")
        case_ids.add(case["case_id"])
        if case["family"] not in TASK_FAMILIES:
            _fail(f"{path}.family: unsupported task family")
        families.add(case["family"])
        if text_sha256(case["prompt"]) != case["prompt_sha256"]:
            _fail(f"{path}.prompt_sha256: prompt hash drift")
        checker = _exact(case["checker"], {"kind", "specification", "specification_sha256", "deterministic"}, f"{path}.checker")
        if checker["kind"] not in {"exact", "json_schema", "regex", "unit_test", "rubric"}:
            _fail(f"{path}.checker.kind: unsupported checker")
        if text_sha256(checker["specification"]) != checker["specification_sha256"]:
            _fail(f"{path}.checker.specification_sha256: checker hash drift")
    required = {"niche_coding", "mathematics", "reasoning"}
    if not required.issubset(families):
        _fail(f"$suite.cases: missing mandatory task families {sorted(required - families)!r}")
    workload_ids: set[str] = set()
    for index, workload in enumerate(suite["workloads"]):
        path = f"$suite.workloads[{index}]"
        _exact(
            workload,
            {"workload_id", "shape", "context_tokens", "output_tokens", "concurrency", "prefix_share_ratio", "repetitions"},
            path,
        )
        if workload["workload_id"] in workload_ids:
            _fail(f"{path}.workload_id: duplicate")
        workload_ids.add(workload["workload_id"])
    if suite["authority"] != {"scope": "saved_task_suite_only", "may_execute_live": False}:
        _fail("$suite.authority: saved-data-only authority is required")


def validate_test_configuration(configuration: Mapping[str, Any]) -> None:
    _exact(
        configuration,
        {
            "schema", "configuration_id", "captured_at", "artifact", "engine", "hardware", "placement",
            "generation", "workload", "background_state", "evidence", "authority",
        },
        "$configuration",
    )
    if configuration["schema"] != CONFIG_SCHEMA:
        _fail(f"$configuration.schema: must equal {CONFIG_SCHEMA!r}")
    _time(configuration["captured_at"], "$configuration.captured_at")
    artifact = _exact(
        configuration["artifact"],
        {"artifact_id", "manifest_sha256", "repository", "revision", "files_sha256", "tokenizer_sha256", "chat_template_sha256", "quantization"},
        "$configuration.artifact",
    )
    for key in ("manifest_sha256", "files_sha256", "tokenizer_sha256", "chat_template_sha256"):
        _sha(artifact[key], f"$configuration.artifact.{key}")
    engine = _exact(
        configuration["engine"],
        {"name", "version", "build_sha256", "runtime_image_sha256", "argv", "environment"},
        "$configuration.engine",
    )
    _sha(engine["build_sha256"], "$configuration.engine.build_sha256")
    _sha(engine["runtime_image_sha256"], "$configuration.engine.runtime_image_sha256")
    hardware = _exact(
        configuration["hardware"],
        {"inventory_sha256", "host_id", "boot_id", "driver_version", "cuda_version", "cpu_model", "logical_cpu_count", "ram_total_bytes", "numa_policy", "gpus", "links_sha256", "power_policy_sha256"},
        "$configuration.hardware",
    )
    for key in ("inventory_sha256", "links_sha256", "power_policy_sha256"):
        _sha(hardware[key], f"$configuration.hardware.{key}")
    gpu_uuids: list[str] = []
    for index, gpu in enumerate(hardware["gpus"]):
        _exact(gpu, {"uuid", "pci_bus_id", "model", "vram_bytes", "power_limit_watts"}, f"$configuration.hardware.gpus[{index}]")
        gpu_uuids.append(gpu["uuid"])
    if len(gpu_uuids) != len(set(gpu_uuids)):
        _fail("$configuration.hardware.gpus: duplicate UUID")
    placement = _exact(
        configuration["placement"],
        {"gpu_uuid_order", "parallelism", "tensor_split", "gpu_layers", "cpu_moe_layers", "cpu_offload_bytes", "disk_offload_bytes", "kv_cache_dtype", "kv_cache_bytes", "memory_utilization_ratio", "swap_policy"},
        "$configuration.placement",
    )
    if placement["gpu_uuid_order"] != gpu_uuids:
        _fail("$configuration.placement.gpu_uuid_order: must equal captured GPU order")
    if not placement["parallelism"]:
        _fail("$configuration.placement.parallelism: at least one mode is required")
    for index, item in enumerate(placement["parallelism"]):
        _exact(item, {"mode", "size"}, f"$configuration.placement.parallelism[{index}]")
    _exact(
        configuration["generation"],
        {"temperature", "top_p", "top_k", "seed", "max_tokens", "reasoning_mode", "structured_output"},
        "$configuration.generation",
    )
    workload = _exact(
        configuration["workload"],
        {"suite_id", "suite_sha256", "case_ids", "workload_id", "warmup_requests", "measured_requests", "duration_seconds"},
        "$configuration.workload",
    )
    _sha(workload["suite_sha256"], "$configuration.workload.suite_sha256")
    _exact(
        configuration["background_state"],
        {"active_services_sha256", "active_processes_sha256", "network_state_sha256", "storage_state_sha256", "notes"},
        "$configuration.background_state",
    )
    for key in ("active_services_sha256", "active_processes_sha256", "network_state_sha256", "storage_state_sha256"):
        _sha(configuration["background_state"][key], f"$configuration.background_state.{key}")
    _exact(configuration["evidence"], {"capture_manifest_sha256", "runner_sha256", "raw_evidence_sha256"}, "$configuration.evidence")
    for key in configuration["evidence"]:
        _sha(configuration["evidence"][key], f"$configuration.evidence.{key}")
    if configuration["authority"] != {"scope": "saved_test_configuration_only", "may_execute_live": False, "may_publish_route": False}:
        _fail("$configuration.authority: saved-data-only authority is required")


def validate_benchmark_profile(profile: Mapping[str, Any], suite: Mapping[str, Any]) -> None:
    _exact(
        profile,
        {
            "schema", "profile_id", "configuration", "configuration_sha256", "suite_id", "suite_sha256",
            "route", "capabilities", "trust_zones", "observed_at", "expires_at", "scaling", "task_scores",
            "performance", "reliability", "resource_cost", "hard_gates_passed", "evidence_sha256", "authority",
        },
        "$profile",
    )
    if profile["schema"] != PROFILE_SCHEMA:
        _fail(f"$profile.schema: must equal {PROFILE_SCHEMA!r}")
    validate_test_configuration(profile["configuration"])
    if canonical_sha256(profile["configuration"]) != profile["configuration_sha256"]:
        _fail("$profile.configuration_sha256: configuration hash drift")
    validate_task_suite(suite)
    if profile["suite_id"] != suite["suite_id"] or profile["suite_sha256"] != canonical_sha256(suite):
        _fail("$profile: task suite identity drift")
    if profile["configuration"]["workload"]["suite_sha256"] != profile["suite_sha256"]:
        _fail("$profile.configuration.workload: suite hash drift")
    _time(profile["observed_at"], "$profile.observed_at")
    if _time(profile["expires_at"], "$profile.expires_at") <= _time(profile["observed_at"], "$profile.observed_at"):
        _fail("$profile.expires_at: must be later than observed_at")
    _exact(profile["route"], {"route_id", "endpoint", "served_model_id", "active_manifest_sha256"}, "$profile.route")
    _sha(profile["route"]["active_manifest_sha256"], "$profile.route.active_manifest_sha256")
    scaling = _exact(
        profile["scaling"],
        {"series_id", "axis", "point_id", "gpu_count", "replica_count", "parallelism_sha256", "cpu_offload_bytes", "disk_offload_bytes", "baseline_point_id", "speedup_ratio", "scaling_efficiency_ratio"},
        "$profile.scaling",
    )
    if scaling["axis"] not in SCALE_AXES:
        _fail("$profile.scaling.axis: unsupported scale axis")
    if scaling["gpu_count"] != len(profile["configuration"]["placement"]["gpu_uuid_order"]):
        _fail("$profile.scaling.gpu_count: does not match configuration")
    if scaling["parallelism_sha256"] != canonical_sha256(profile["configuration"]["placement"]["parallelism"]):
        _fail("$profile.scaling.parallelism_sha256: parallelism hash drift")
    score_families: set[str] = set()
    for index, score in enumerate(profile["task_scores"]):
        _exact(score, {"family", "case_ids", "score", "pass_rate", "sample_count", "standard_error", "checker_evidence_sha256"}, f"$profile.task_scores[{index}]")
        if score["family"] in score_families:
            _fail(f"$profile.task_scores[{index}].family: duplicate")
        score_families.add(score["family"])
        _sha(score["checker_evidence_sha256"], f"$profile.task_scores[{index}].checker_evidence_sha256")
        if not 0 <= score["score"] <= 1 or not 0 <= score["pass_rate"] <= 1:
            _fail(f"$profile.task_scores[{index}]: scores must be from 0 to 1")
    workload_ids: set[str] = set()
    for index, performance in enumerate(profile["performance"]):
        _exact(
            performance,
            {"workload_id", "context_tokens", "output_tokens", "concurrency", "goodput_tokens_per_second", "e2e_p95_ms", "ttft_p95_ms", "tpot_p95_ms", "energy_joules_per_output_token", "startup_seconds", "completed_requests", "failed_requests", "thermal_throttle_events"},
            f"$profile.performance[{index}]",
        )
        if performance["workload_id"] in workload_ids:
            _fail(f"$profile.performance[{index}].workload_id: duplicate")
        workload_ids.add(performance["workload_id"])
    _exact(profile["reliability"], {"request_success_ratio", "restoration_verified", "oom_events", "preemptions"}, "$profile.reliability")
    _exact(profile["resource_cost"], {"gpu_count", "total_vram_bytes", "host_ram_bytes", "cpu_threads", "power_limit_watts_total"}, "$profile.resource_cost")
    if profile["resource_cost"]["gpu_count"] != scaling["gpu_count"]:
        _fail("$profile.resource_cost.gpu_count: scaling mismatch")
    _sha(profile["evidence_sha256"], "$profile.evidence_sha256")
    if profile["authority"] != {"scope": "saved_benchmark_profile_only", "may_claim_unmeasured": False, "may_publish_route": False}:
        _fail("$profile.authority: saved benchmark authority is required")


def validate_route_request(request: Mapping[str, Any]) -> None:
    _exact(
        request,
        {"schema", "request_id", "as_of", "suite_id", "suite_sha256", "task_requirements", "capabilities", "workload_id", "context_tokens", "output_tokens", "concurrency", "trust_zone", "allowed_scale_axes", "limits", "objective_order", "authority"},
        "$request",
    )
    if request["schema"] != REQUEST_SCHEMA:
        _fail(f"$request.schema: must equal {REQUEST_SCHEMA!r}")
    _time(request["as_of"], "$request.as_of")
    _sha(request["suite_sha256"], "$request.suite_sha256")
    seen: set[str] = set()
    for index, item in enumerate(request["task_requirements"]):
        _exact(item, {"family", "minimum_score", "minimum_pass_rate", "minimum_samples", "weight"}, f"$request.task_requirements[{index}]")
        if item["family"] in seen:
            _fail(f"$request.task_requirements[{index}].family: duplicate")
        seen.add(item["family"])
    if not set(request["allowed_scale_axes"]).issubset(SCALE_AXES):
        _fail("$request.allowed_scale_axes: unsupported scale axis")
    limits = _exact(request["limits"], {"max_e2e_p95_ms", "max_energy_joules_per_output_token", "max_gpu_count", "max_host_ram_bytes", "minimum_request_success_ratio", "require_restoration"}, "$request.limits")
    if limits["max_gpu_count"] < 0:
        _fail("$request.limits.max_gpu_count: non-negative value required")
    if not request["objective_order"] or len(request["objective_order"]) != len(set(request["objective_order"])):
        _fail("$request.objective_order: non-empty unique list required")
    if not set(request["objective_order"]).issubset(OBJECTIVES):
        _fail("$request.objective_order: unsupported objective")
    if request["authority"] != {"scope": "offline_route_selection_only", "may_contact_endpoint": False, "may_mutate_services": False}:
        _fail("$request.authority: offline selection authority is required")


def validate_route_decision(decision: Mapping[str, Any]) -> None:
    _exact(
        decision,
        {"schema", "decision_id", "decided_at", "request_id", "request_sha256", "suite_sha256", "profile_set_sha256", "selected", "candidates", "outcome", "authority"},
        "$decision",
    )
    if decision["schema"] != DECISION_SCHEMA:
        _fail(f"$decision.schema: must equal {DECISION_SCHEMA!r}")
    _time(decision["decided_at"], "$decision.decided_at")
    for key in ("request_sha256", "suite_sha256", "profile_set_sha256"):
        _sha(decision[key], f"$decision.{key}")
    if not decision["candidates"]:
        _fail("$decision.candidates: at least one candidate is required")
    eligible: list[Mapping[str, Any]] = []
    route_ids: set[str] = set()
    for index, candidate in enumerate(decision["candidates"]):
        path = f"$decision.candidates[{index}]"
        _exact(
            candidate,
            {"profile_id", "route_id", "configuration_id", "scale_axis", "scaling_series_id", "eligible", "exclusion_reasons", "metrics", "rank"},
            path,
        )
        if candidate["route_id"] in route_ids:
            _fail(f"{path}.route_id: duplicate")
        route_ids.add(candidate["route_id"])
        _exact(candidate["metrics"], set(OBJECTIVES), f"{path}.metrics")
        if candidate["eligible"]:
            if candidate["exclusion_reasons"] or not isinstance(candidate["rank"], int):
                _fail(f"{path}: eligible candidate must have a rank and no exclusions")
            eligible.append(candidate)
        elif not candidate["exclusion_reasons"] or candidate["rank"] is not None:
            _fail(f"{path}: excluded candidate must have reasons and no rank")
    ranks = sorted(item["rank"] for item in eligible)
    if ranks != list(range(1, len(eligible) + 1)):
        _fail("$decision.candidates: eligible ranks must be consecutive")
    if decision["outcome"] == "selected":
        selected = _exact(decision["selected"], {"profile_id", "route_id", "configuration_id"}, "$decision.selected")
        winner = next((item for item in eligible if item["rank"] == 1), None)
        if winner is None or any(selected[key] != winner[key] for key in selected):
            _fail("$decision.selected: must match eligible rank 1")
    elif decision["outcome"] == "no_eligible_route":
        if decision["selected"] is not None or eligible:
            _fail("$decision: no-route outcome cannot contain an eligible selection")
    else:
        _fail("$decision.outcome: unsupported outcome")
    if decision["authority"] != {"scope": "saved_route_decision_only", "may_contact_endpoint": False, "may_mutate_services": False, "may_publish_route": False}:
        _fail("$decision.authority: saved decision authority is required")


def _performance(profile: Mapping[str, Any], workload_id: str) -> Mapping[str, Any] | None:
    return next((item for item in profile["performance"] if item["workload_id"] == workload_id), None)


def _eligibility(request: Mapping[str, Any], profile: Mapping[str, Any]) -> tuple[list[str], dict[str, float]]:
    reasons: list[str] = []
    as_of = _time(request["as_of"], "$request.as_of")
    if _time(profile["expires_at"], "$profile.expires_at") < as_of:
        reasons.append("stale_benchmark_evidence")
    if not profile["hard_gates_passed"]:
        reasons.append("hard_gates_failed")
    if profile["scaling"]["axis"] not in request["allowed_scale_axes"]:
        reasons.append("scale_axis_not_allowed")
    if not set(request["capabilities"]).issubset(profile["capabilities"]):
        reasons.append("capability_missing")
    if request["trust_zone"] not in profile["trust_zones"]:
        reasons.append("trust_zone_mismatch")
    performance = _performance(profile, request["workload_id"])
    if performance is None:
        reasons.append("workload_not_measured")
        performance = {
            "context_tokens": 0,
            "output_tokens": 0,
            "concurrency": 0,
            "goodput_tokens_per_second": 0.0,
            "e2e_p95_ms": 0.0,
            "energy_joules_per_output_token": 0.0,
            "startup_seconds": 0.0,
        }
    else:
        if request["context_tokens"] > performance["context_tokens"]:
            reasons.append("context_limit_exceeded")
        if request["output_tokens"] > performance["output_tokens"]:
            reasons.append("output_limit_exceeded")
        if request["concurrency"] > performance["concurrency"]:
            reasons.append("concurrency_limit_exceeded")
    limits = request["limits"]
    resource = profile["resource_cost"]
    reliability = profile["reliability"]
    if performance["e2e_p95_ms"] > limits["max_e2e_p95_ms"]:
        reasons.append("latency_limit_exceeded")
    if performance["energy_joules_per_output_token"] > limits["max_energy_joules_per_output_token"]:
        reasons.append("energy_limit_exceeded")
    if resource["gpu_count"] > limits["max_gpu_count"]:
        reasons.append("gpu_limit_exceeded")
    if resource["host_ram_bytes"] > limits["max_host_ram_bytes"]:
        reasons.append("host_ram_limit_exceeded")
    if reliability["request_success_ratio"] < limits["minimum_request_success_ratio"]:
        reasons.append("reliability_limit_failed")
    if limits["require_restoration"] and not reliability["restoration_verified"]:
        reasons.append("restoration_not_verified")
    scores = {item["family"]: item for item in profile["task_scores"]}
    weighted = 0.0
    total_weight = 0.0
    for requirement in request["task_requirements"]:
        score = scores.get(requirement["family"])
        if score is None:
            reasons.append(f"task_family_unmeasured:{requirement['family']}")
            continue
        if score["score"] < requirement["minimum_score"]:
            reasons.append(f"task_score_below_floor:{requirement['family']}")
        if score["pass_rate"] < requirement["minimum_pass_rate"]:
            reasons.append(f"task_pass_rate_below_floor:{requirement['family']}")
        if score["sample_count"] < requirement["minimum_samples"]:
            reasons.append(f"task_samples_below_floor:{requirement['family']}")
        weighted += score["score"] * requirement["weight"]
        total_weight += requirement["weight"]
    quality = weighted / total_weight if total_weight else 0.0
    metrics = {
        "quality_score": quality,
        "goodput_tokens_per_second": float(performance["goodput_tokens_per_second"]),
        "e2e_p95_ms": float(performance["e2e_p95_ms"]),
        "energy_joules_per_output_token": float(performance["energy_joules_per_output_token"]),
        "gpu_count": float(resource["gpu_count"]),
        "host_ram_bytes": float(resource["host_ram_bytes"]),
        "startup_seconds": float(performance["startup_seconds"]),
    }
    return sorted(set(reasons)), metrics


def select_route(request: Mapping[str, Any], profiles: Sequence[Mapping[str, Any]], suite: Mapping[str, Any]) -> dict[str, Any]:
    """Select one saved route with explicit exclusions and no side effects."""
    validate_task_suite(suite)
    validate_route_request(request)
    if request["suite_id"] != suite["suite_id"] or request["suite_sha256"] != canonical_sha256(suite):
        _fail("$request: task suite identity drift")
    if not profiles:
        _fail("$profiles: at least one profile is required")
    profile_ids: set[str] = set()
    route_ids: set[str] = set()
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        validate_benchmark_profile(profile, suite)
        if profile["profile_id"] in profile_ids:
            _fail("$profiles: duplicate profile identity")
        if profile["route"]["route_id"] in route_ids:
            _fail("$profiles: duplicate active route identity")
        profile_ids.add(profile["profile_id"])
        route_ids.add(profile["route"]["route_id"])
        reasons, metrics = _eligibility(request, profile)
        rows.append({
            "profile_id": profile["profile_id"],
            "route_id": profile["route"]["route_id"],
            "configuration_id": profile["configuration"]["configuration_id"],
            "scale_axis": profile["scaling"]["axis"],
            "scaling_series_id": profile["scaling"]["series_id"],
            "eligible": not reasons,
            "exclusion_reasons": reasons,
            "metrics": metrics,
        })

    def rank_key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        values: list[Any] = []
        for name in request["objective_order"]:
            value = row["metrics"][name]
            values.append(-value if OBJECTIVES[name] == "max" else value)
        values.extend((row["route_id"], row["configuration_id"]))
        return tuple(values)

    eligible = sorted((row for row in rows if row["eligible"]), key=rank_key)
    for rank, row in enumerate(eligible, 1):
        row["rank"] = rank
    for row in rows:
        if not row["eligible"]:
            row["rank"] = None
    rows.sort(key=lambda row: (not row["eligible"], row["rank"] or 10**9, row["route_id"]))
    selected = eligible[0] if eligible else None
    decision = {
        "schema": DECISION_SCHEMA,
        "decision_id": f"decision-{canonical_sha256({'request': request, 'profiles': sorted(p['profile_id'] for p in profiles)})[:24]}",
        "decided_at": request["as_of"],
        "request_id": request["request_id"],
        "request_sha256": canonical_sha256(request),
        "suite_sha256": request["suite_sha256"],
        "profile_set_sha256": canonical_sha256(sorted((p["profile_id"], p["evidence_sha256"]) for p in profiles)),
        "selected": None if selected is None else {
            "profile_id": selected["profile_id"],
            "route_id": selected["route_id"],
            "configuration_id": selected["configuration_id"],
        },
        "candidates": rows,
        "outcome": "selected" if selected else "no_eligible_route",
        "authority": {"scope": "saved_route_decision_only", "may_contact_endpoint": False, "may_mutate_services": False, "may_publish_route": False},
    }
    validate_route_decision(decision)
    return decision


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("suite", type=Path)
    parser.add_argument("profiles", type=Path, nargs="+")
    args = parser.parse_args(argv)
    print(json.dumps(select_route(_read(args.request), [_read(path) for path in args.profiles], _read(args.suite)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
