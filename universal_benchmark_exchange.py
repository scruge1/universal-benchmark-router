"""Offline cross-rig benchmark exchange and universal evidence compiler.

This module separates portable model/task evidence from system-specific
performance. It reads saved documents only and has no transport, execution,
publication, endpoint, or host authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from universal_model_router import (
    canonical_sha256,
    select_route,
    validate_benchmark_profile,
    validate_route_decision,
    validate_task_suite,
    validate_test_configuration,
)


CONTRIBUTION_SCHEMA = "universal-benchmark-contribution/v1"
REQUEST_SCHEMA = "universal-benchmark-request/v1"
RESULT_ENVELOPE_SCHEMA = "universal-benchmark-result-envelope/v1"
ACK_SCHEMA = "universal-benchmark-acknowledgement/v1"
CATALOG_SCHEMA = "universal-model-evidence-catalog/v1"
REQUEST_BOUND_CATALOG_SCHEMA = "universal-request-bound-evidence-catalog/v1"
UNIVERSAL_DECISION_SCHEMA = "universal-route-decision/v1"
REQUEST_BOUND_DECISION_SCHEMA = "request-bound-universal-route-decision/v1"
QUALITY_DIMENSIONS = (
    "correctness",
    "instruction_adherence",
    "groundedness",
    "tool_execution",
    "failure_recovery",
    "safety",
)
COMPARISON_AXES = {
    "model_variant",
    "fine_tune",
    "quantization",
    "context",
    "temperature",
    "top_p",
    "engine",
    "within_host_vertical",
    "cross_host_horizontal",
    "cross_site_evidence",
}


class ExchangeContractError(ValueError):
    """A saved cross-rig evidence document is malformed or inconsistent."""


def _fail(message: str) -> None:
    raise ExchangeContractError(message)


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
        _fail(f"{path}: timestamp is required")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExchangeContractError(f"{path}: invalid timestamp") from exc
    if result.tzinfo is None:
        _fail(f"{path}: timezone is required")
    return result.astimezone(timezone.utc)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _verify_attestation(
    attestation: Mapping[str, Any],
    payload: Mapping[str, Any],
    public_keys: Mapping[str, str],
    path: str,
) -> None:
    key_id = attestation["key_id"]
    encoded_key = public_keys.get(key_id)
    if encoded_key is None:
        _fail(f"{path}: enrolled public key is required")
    try:
        public_key_bytes = base64.b64decode(encoded_key, validate=True)
        signature = base64.b64decode(attestation["signature"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ExchangeContractError(f"{path}: invalid base64 key or signature") from exc
    if hashlib.sha256(public_key_bytes).hexdigest() != key_id:
        _fail(f"{path}: public key identity drift")
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(signature, _canonical_bytes(payload))
    except (ValueError, InvalidSignature) as exc:
        raise ExchangeContractError(f"{path}: Ed25519 signature verification failed") from exc


def request_payload(request: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in request.items() if key != "attestation"}


def result_envelope_payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "attestation"}


def validate_benchmark_request(request: Mapping[str, Any], capability_contract: Mapping[str, Any]) -> None:
    """Validate one inert, portable benchmark request against the pinned capability contract."""
    _exact(
        request,
        {
            "schema", "request_id", "issued_at", "expires_at", "issuer",
            "capability_contract", "lane", "task_pack", "test_cells",
            "comparison_axis", "trial_policy", "required_metrics",
            "contribution_policy", "attestation", "authority",
        },
        "$request",
    )
    if request["schema"] != REQUEST_SCHEMA:
        _fail(f"$request.schema: must equal {REQUEST_SCHEMA!r}")
    if not isinstance(request["request_id"], str) or not request["request_id"]:
        _fail("$request.request_id: non-empty identity is required")
    issued_at = _time(request["issued_at"], "$request.issued_at")
    expires_at = _time(request["expires_at"], "$request.expires_at")
    if expires_at <= issued_at:
        _fail("$request.expires_at: must be after issued_at")
    issuer = _exact(request["issuer"], {"issuer_id", "public_key_id"}, "$request.issuer")
    if not issuer["issuer_id"]:
        _fail("$request.issuer.issuer_id: non-empty identity is required")
    _sha(issuer["public_key_id"], "$request.issuer.public_key_id")

    contract_ref = _exact(
        request["capability_contract"],
        {"contract_id", "version", "sha256"},
        "$request.capability_contract",
    )
    _sha(contract_ref["sha256"], "$request.capability_contract.sha256")
    if (
        contract_ref["contract_id"] != capability_contract.get("contract_id")
        or contract_ref["version"] != capability_contract.get("version")
        or contract_ref["sha256"] != canonical_sha256(capability_contract)
    ):
        _fail("$request.capability_contract: identity drift")

    lane_ref = _exact(
        request["lane"],
        {
            "lane_id", "dimension", "evidence_scope", "tier", "claim_cap",
            "benchmark_source_ids", "required_subtests", "objective_checkers",
        },
        "$request.lane",
    )
    lanes = {lane["lane_id"]: lane for lane in capability_contract.get("capability_lanes", [])}
    lane = lanes.get(lane_ref["lane_id"])
    if lane is None or lane_ref != {key: lane[key] for key in lane_ref}:
        _fail("$request.lane: unknown lane or contract drift")

    task_pack = _exact(
        request["task_pack"],
        {
            "pack_id", "revision", "manifest_sha256", "visibility",
            "redistribution_allowed", "cross_site_allowed", "case_ids",
        },
        "$request.task_pack",
    )
    _sha(task_pack["manifest_sha256"], "$request.task_pack.manifest_sha256")
    if task_pack["visibility"] not in {"public_pinned", "private_sealed_local"}:
        _fail("$request.task_pack.visibility: unsupported visibility")
    if not task_pack["case_ids"] or len(task_pack["case_ids"]) != len(set(task_pack["case_ids"])):
        _fail("$request.task_pack.case_ids: non-empty unique case identities are required")
    if task_pack["visibility"] == "public_pinned" and (
        task_pack["redistribution_allowed"] is not True or task_pack["cross_site_allowed"] is not True
    ):
        _fail("$request.task_pack: public pinned packs must permit cross-site redistribution")
    if task_pack["visibility"] == "private_sealed_local" and task_pack["cross_site_allowed"] is not False:
        _fail("$request.task_pack: sealed local packs cannot authorize cross-site execution")

    if request["comparison_axis"] not in COMPARISON_AXES:
        _fail("$request.comparison_axis: unsupported comparison axis")
    if not isinstance(request["test_cells"], list) or not request["test_cells"]:
        _fail("$request.test_cells: at least one exact cell is required")
    cell_ids: set[str] = set()
    for index, cell in enumerate(request["test_cells"]):
        path = f"$request.test_cells[{index}]"
        _exact(
            cell,
            {
                "cell_id", "model_variant_sha256", "fine_tune_id", "quantization",
                "context_tokens", "temperature", "top_p", "engine_name",
                "evaluation_protocol_sha256", "system_constraints",
            },
            path,
        )
        if not cell["cell_id"] or cell["cell_id"] in cell_ids:
            _fail(f"{path}.cell_id: non-empty unique identity is required")
        cell_ids.add(cell["cell_id"])
        _sha(cell["model_variant_sha256"], f"{path}.model_variant_sha256")
        _sha(cell["evaluation_protocol_sha256"], f"{path}.evaluation_protocol_sha256")
        if not isinstance(cell["context_tokens"], int) or isinstance(cell["context_tokens"], bool) or cell["context_tokens"] < 1:
            _fail(f"{path}.context_tokens: positive integer is required")
        for name in ("temperature", "top_p"):
            if not isinstance(cell[name], (int, float)) or isinstance(cell[name], bool) or cell[name] < 0:
                _fail(f"{path}.{name}: non-negative number is required")
        if cell["top_p"] > 1:
            _fail(f"{path}.top_p: must not exceed 1")
        constraints = _exact(
            cell["system_constraints"],
            {
                "system_class_sha256", "gpu_count", "gpu_model_set_sha256",
                "gpu_uuid_order_sha256", "parallelism_sha256", "cpu_offload_bytes",
                "power_limit_watts_total",
            },
            f"{path}.system_constraints",
        )
        for name in (
            "system_class_sha256", "gpu_model_set_sha256", "gpu_uuid_order_sha256",
            "parallelism_sha256",
        ):
            if constraints[name] is not None:
                _sha(constraints[name], f"{path}.system_constraints.{name}")
        for name in ("gpu_count", "cpu_offload_bytes", "power_limit_watts_total"):
            if constraints[name] is not None and (
                not isinstance(constraints[name], int) or isinstance(constraints[name], bool) or constraints[name] < 0
            ):
                _fail(f"{path}.system_constraints.{name}: null or non-negative integer is required")

    if len(request["test_cells"]) > 1:
        allowed_differences = {
            "model_variant": {"cell_id", "model_variant_sha256", "fine_tune_id", "quantization"},
            "fine_tune": {"cell_id", "model_variant_sha256", "fine_tune_id"},
            "quantization": {"cell_id", "model_variant_sha256", "quantization"},
            "context": {"cell_id", "context_tokens"},
            "temperature": {"cell_id", "temperature", "evaluation_protocol_sha256"},
            "top_p": {"cell_id", "top_p", "evaluation_protocol_sha256"},
            "engine": {"cell_id", "engine_name", "evaluation_protocol_sha256"},
            "within_host_vertical": {"cell_id", "system_constraints"},
            "cross_host_horizontal": {"cell_id", "system_constraints"},
            "cross_site_evidence": {"cell_id", "system_constraints"},
        }[request["comparison_axis"]]
        baseline = request["test_cells"][0]
        fixed_keys = set(baseline) - allowed_differences
        for index, cell in enumerate(request["test_cells"][1:], start=1):
            if any(cell[key] != baseline[key] for key in fixed_keys):
                _fail(f"$request.test_cells[{index}]: more than the declared comparison axis changed")

    trial = _exact(
        request["trial_policy"],
        {"minimum_trials", "maximum_trials", "seeds", "one_axis_change_per_comparison"},
        "$request.trial_policy",
    )
    if (
        not isinstance(trial["minimum_trials"], int)
        or isinstance(trial["minimum_trials"], bool)
        or trial["minimum_trials"] < lane["minimum_trials"]
        or not isinstance(trial["maximum_trials"], int)
        or trial["maximum_trials"] < trial["minimum_trials"]
    ):
        _fail("$request.trial_policy: invalid trial bounds")
    if len(trial["seeds"]) < trial["minimum_trials"] or len(trial["seeds"]) != len(set(trial["seeds"])):
        _fail("$request.trial_policy.seeds: enough unique seeds are required")
    if any(not isinstance(seed, int) or isinstance(seed, bool) for seed in trial["seeds"]):
        _fail("$request.trial_policy.seeds: integers are required")
    if trial["one_axis_change_per_comparison"] is not True:
        _fail("$request.trial_policy.one_axis_change_per_comparison: must be true")
    if request["required_metrics"] != capability_contract.get("required_metrics"):
        _fail("$request.required_metrics: capability contract drift")

    policy = _exact(
        request["contribution_policy"],
        {
            "minimum_distinct_contributors", "minimum_independent_validators_per_submission",
            "signature_algorithm", "retain_site_identity", "system_performance_pooling",
        },
        "$request.contribution_policy",
    )
    if (
        policy["minimum_distinct_contributors"] < 2
        or policy["minimum_independent_validators_per_submission"] < 1
        or policy["signature_algorithm"] != "ed25519"
        or policy["retain_site_identity"] is not True
        or policy["system_performance_pooling"] != "forbidden"
    ):
        _fail("$request.contribution_policy: unsafe universal evidence policy")
    attestation = _exact(request["attestation"], {"algorithm", "key_id", "payload_sha256", "signature"}, "$request.attestation")
    if attestation["algorithm"] != "ed25519" or attestation["key_id"] != issuer["public_key_id"]:
        _fail("$request.attestation: issuer key mismatch or unsupported algorithm")
    _sha(attestation["payload_sha256"], "$request.attestation.payload_sha256")
    if attestation["payload_sha256"] != canonical_sha256(request_payload(request)):
        _fail("$request.attestation.payload_sha256: signed payload drift")
    if not isinstance(attestation["signature"], str) or not attestation["signature"]:
        _fail("$request.attestation.signature: non-empty detached signature is required")
    if request["authority"] != {
        "scope": "saved_benchmark_request_only",
        "may_execute_live": False,
        "may_accept_submission": False,
        "may_publish": False,
    }:
        _fail("$request.authority: inert saved request authority is required")


def model_variant(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return the cross-rig identity of exact model behavior inputs."""
    artifact = profile["configuration"]["artifact"]
    return {
        "artifact_id": artifact["artifact_id"],
        "manifest_sha256": artifact["manifest_sha256"],
        "revision": artifact["revision"],
        "files_sha256": artifact["files_sha256"],
        "tokenizer_sha256": artifact["tokenizer_sha256"],
        "chat_template_sha256": artifact["chat_template_sha256"],
        "quantization": artifact["quantization"],
    }


def evaluation_protocol(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Return task controls that must match before scores can aggregate."""
    configuration = profile["configuration"]
    return {
        "suite_id": profile["suite_id"],
        "suite_sha256": profile["suite_sha256"],
        "case_ids": sorted(configuration["workload"]["case_ids"]),
        "engine_name": configuration["engine"]["name"],
        "engine_version": configuration["engine"]["version"],
        "engine_build_sha256": configuration["engine"]["build_sha256"],
        "kv_cache_dtype": configuration["placement"]["kv_cache_dtype"],
        "generation": configuration["generation"],
    }


def validate_contribution(contribution: Mapping[str, Any], suite: Mapping[str, Any]) -> None:
    _exact(
        contribution,
        {
            "schema", "submission_id", "observed_at", "contributor", "site", "suite_id", "suite_sha256",
            "model_variant", "model_variant_sha256", "evaluation_protocol", "evaluation_protocol_sha256",
            "configuration_id", "configuration_sha256", "task_records", "system_result", "evidence",
            "disclosure", "attestation", "authority",
        },
        "$contribution",
    )
    if contribution["schema"] != CONTRIBUTION_SCHEMA:
        _fail(f"$contribution.schema: must equal {CONTRIBUTION_SCHEMA!r}")
    _time(contribution["observed_at"], "$contribution.observed_at")
    validate_task_suite(suite)
    if contribution["suite_id"] != suite["suite_id"] or contribution["suite_sha256"] != canonical_sha256(suite):
        _fail("$contribution: suite identity drift")
    contributor = _exact(contribution["contributor"], {"contributor_id", "public_key_id"}, "$contribution.contributor")
    site = _exact(contribution["site"], {"site_id", "system_class_sha256", "hardware_inventory_sha256"}, "$contribution.site")
    if not contributor["contributor_id"] or not site["site_id"]:
        _fail("$contribution: contributor and site identities are required")
    _sha(contributor["public_key_id"], "$contribution.contributor.public_key_id")
    _sha(site["system_class_sha256"], "$contribution.site.system_class_sha256")
    _sha(site["hardware_inventory_sha256"], "$contribution.site.hardware_inventory_sha256")
    if canonical_sha256(contribution["model_variant"]) != contribution["model_variant_sha256"]:
        _fail("$contribution.model_variant_sha256: model variant drift")
    if canonical_sha256(contribution["evaluation_protocol"]) != contribution["evaluation_protocol_sha256"]:
        _fail("$contribution.evaluation_protocol_sha256: protocol drift")
    variant = _exact(
        contribution["model_variant"],
        {
            "artifact_id", "manifest_sha256", "revision", "files_sha256",
            "tokenizer_sha256", "chat_template_sha256", "quantization",
        },
        "$contribution.model_variant",
    )
    for key in ("manifest_sha256", "files_sha256", "tokenizer_sha256", "chat_template_sha256"):
        _sha(variant[key], f"$contribution.model_variant.{key}")
    protocol = _exact(
        contribution["evaluation_protocol"],
        {
            "suite_id", "suite_sha256", "case_ids", "engine_name", "engine_version",
            "engine_build_sha256", "kv_cache_dtype", "generation",
        },
        "$contribution.evaluation_protocol",
    )
    if protocol["suite_id"] != suite["suite_id"] or protocol["suite_sha256"] != canonical_sha256(suite):
        _fail("$contribution.evaluation_protocol: suite identity drift")
    if sorted(protocol["case_ids"]) != sorted(case["case_id"] for case in suite["cases"]):
        _fail("$contribution.evaluation_protocol.case_ids: incomplete or unknown case set")
    _sha(protocol["engine_build_sha256"], "$contribution.evaluation_protocol.engine_build_sha256")
    for key in ("model_variant_sha256", "evaluation_protocol_sha256", "configuration_sha256"):
        _sha(contribution[key], f"$contribution.{key}")
    cases = {case["case_id"]: case["family"] for case in suite["cases"]}
    seen: set[tuple[str, str, int]] = set()
    seen_cases: set[str] = set()
    if not contribution["task_records"]:
        _fail("$contribution.task_records: at least one record is required")
    for index, record in enumerate(contribution["task_records"]):
        path = f"$contribution.task_records[{index}]"
        _exact(
            record,
            {
                "case_id", "family", "trial_id", "repetition_index", "seed", "score", "passed",
                "attempt_count", "prompt_sha256", "checker_specification_sha256", "output_sha256",
                "checker_receipt_sha256", "measurements",
            },
            path,
        )
        record_identity = (record["case_id"], record["trial_id"], record["repetition_index"])
        if record_identity in seen:
            _fail(f"{path}: duplicate case, trial, and repetition identity")
        seen.add(record_identity)
        seen_cases.add(record["case_id"])
        if cases.get(record["case_id"]) != record["family"]:
            _fail(f"{path}: case or family does not match suite")
        if not isinstance(record["score"], (int, float)) or isinstance(record["score"], bool) or not 0 <= record["score"] <= 1:
            _fail(f"{path}.score: number from 0 to 1 is required")
        if not isinstance(record["passed"], bool) or not isinstance(record["attempt_count"], int) or record["attempt_count"] < 1:
            _fail(f"{path}: passed and positive attempt count are required")
        if not isinstance(record["trial_id"], str) or not record["trial_id"]:
            _fail(f"{path}.trial_id: non-empty identity is required")
        if not isinstance(record["repetition_index"], int) or record["repetition_index"] < 0:
            _fail(f"{path}.repetition_index: non-negative integer is required")
        if not isinstance(record["seed"], int):
            _fail(f"{path}.seed: integer is required")
        case = next(item for item in suite["cases"] if item["case_id"] == record["case_id"])
        if record["prompt_sha256"] != case["prompt_sha256"]:
            _fail(f"{path}.prompt_sha256: prompt identity drift")
        if record["checker_specification_sha256"] != case["checker"]["specification_sha256"]:
            _fail(f"{path}.checker_specification_sha256: checker identity drift")
        for key in ("prompt_sha256", "checker_specification_sha256", "output_sha256", "checker_receipt_sha256"):
            _sha(record[key], f"{path}.{key}")
        measurements = _exact(
            record["measurements"],
            {
                "quality", "unsupported_claim_count", "tool_call_count", "tool_failure_count",
                "recovered_tool_failure_count", "input_tokens", "output_tokens", "reasoning_tokens",
                "wall_time_ms", "grader_kind", "grader_identity_sha256",
            },
            f"{path}.measurements",
        )
        quality = _exact(measurements["quality"], set(QUALITY_DIMENSIONS), f"{path}.measurements.quality")
        if quality["correctness"] is None:
            _fail(f"{path}.measurements.quality.correctness: required for every task")
        for name, value in quality.items():
            if value is not None and (
                not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1
            ):
                _fail(f"{path}.measurements.quality.{name}: null or number from 0 to 1 is required")
        for name in (
            "unsupported_claim_count", "tool_call_count", "tool_failure_count",
            "recovered_tool_failure_count", "input_tokens", "output_tokens", "reasoning_tokens",
        ):
            if not isinstance(measurements[name], int) or isinstance(measurements[name], bool) or measurements[name] < 0:
                _fail(f"{path}.measurements.{name}: non-negative integer is required")
        if measurements["tool_failure_count"] > measurements["tool_call_count"]:
            _fail(f"{path}.measurements: tool failures exceed tool calls")
        if measurements["recovered_tool_failure_count"] > measurements["tool_failure_count"]:
            _fail(f"{path}.measurements: recovered failures exceed tool failures")
        if not isinstance(measurements["wall_time_ms"], (int, float)) or isinstance(measurements["wall_time_ms"], bool) or measurements["wall_time_ms"] < 0:
            _fail(f"{path}.measurements.wall_time_ms: non-negative number is required")
        if measurements["grader_kind"] not in {"deterministic", "human", "model_judge", "hybrid"}:
            _fail(f"{path}.measurements.grader_kind: unsupported grader")
        _sha(measurements["grader_identity_sha256"], f"{path}.measurements.grader_identity_sha256")
    if seen_cases != set(cases):
        _fail("$contribution.task_records: incomplete case coverage")
    system = _exact(
        contribution["system_result"],
        {"workload_id", "goodput_tokens_per_second", "e2e_p95_ms", "energy_joules_per_output_token", "request_success_ratio", "performance_evidence_sha256"},
        "$contribution.system_result",
    )
    _sha(system["performance_evidence_sha256"], "$contribution.system_result.performance_evidence_sha256")
    for name in (
        "goodput_tokens_per_second", "e2e_p95_ms", "energy_joules_per_output_token",
        "request_success_ratio",
    ):
        value = system[name]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            _fail(f"$contribution.system_result.{name}: non-negative number is required")
    if system["request_success_ratio"] > 1:
        _fail("$contribution.system_result.request_success_ratio: must not exceed 1")
    evidence = _exact(contribution["evidence"], {"raw_manifest_sha256", "runner_sha256", "configuration_capture_sha256"}, "$contribution.evidence")
    for key in evidence:
        _sha(evidence[key], f"$contribution.evidence.{key}")
    disclosure = _exact(contribution["disclosure"], {"license", "redistribution_allowed", "contains_private_prompts", "contains_personal_data"}, "$contribution.disclosure")
    if not disclosure["redistribution_allowed"] or disclosure["contains_private_prompts"] or disclosure["contains_personal_data"]:
        _fail("$contribution.disclosure: contribution is not safe for universal redistribution")
    attestation = _exact(contribution["attestation"], {"algorithm", "key_id", "payload_sha256", "signature"}, "$contribution.attestation")
    if attestation["algorithm"] != "ed25519":
        _fail("$contribution.attestation.algorithm: ed25519 is required")
    if attestation["key_id"] != contributor["public_key_id"]:
        _fail("$contribution.attestation.key_id: contributor key mismatch")
    _sha(attestation["payload_sha256"], "$contribution.attestation.payload_sha256")
    if attestation["payload_sha256"] != canonical_sha256(contribution_payload(contribution)):
        _fail("$contribution.attestation.payload_sha256: signed payload drift")
    if not isinstance(attestation["signature"], str) or not attestation["signature"]:
        _fail("$contribution.attestation.signature: non-empty detached signature is required")
    if contribution["authority"] != {"scope": "saved_untrusted_contribution_only", "may_enter_catalog": False}:
        _fail("$contribution.authority: untrusted saved contribution authority is required")


def contribution_payload(contribution: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in contribution.items() if key != "attestation"}


def validate_result_envelope(
    result: Mapping[str, Any],
    request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
    suite: Mapping[str, Any],
) -> None:
    """Bind one signed contribution to one exact standardized request cell."""
    validate_benchmark_request(request, capability_contract)
    _exact(
        result,
        {
            "schema", "result_id", "request_id", "request_sha256", "cell_id",
            "observed_context_tokens", "configuration", "contribution", "attestation", "authority",
        },
        "$result",
    )
    if result["schema"] != RESULT_ENVELOPE_SCHEMA:
        _fail(f"$result.schema: must equal {RESULT_ENVELOPE_SCHEMA!r}")
    if result["request_id"] != request["request_id"] or result["request_sha256"] != canonical_sha256(request):
        _fail("$result: request binding drift")
    _sha(result["request_sha256"], "$result.request_sha256")
    cells = {cell["cell_id"]: cell for cell in request["test_cells"]}
    cell = cells.get(result["cell_id"])
    if cell is None:
        _fail("$result.cell_id: unknown request cell")
    contribution = result["contribution"]
    validate_contribution(contribution, suite)
    configuration = result["configuration"]
    validate_test_configuration(configuration)
    if canonical_sha256(configuration) != contribution["configuration_sha256"]:
        _fail("$result.configuration: contribution configuration drift")
    if configuration["configuration_id"] != contribution["configuration_id"]:
        _fail("$result.configuration.configuration_id: contribution identity drift")
    if configuration["hardware"]["inventory_sha256"] != contribution["site"]["hardware_inventory_sha256"]:
        _fail("$result.configuration.hardware.inventory_sha256: site identity drift")
    if canonical_sha256(model_variant({"configuration": configuration})) != contribution["model_variant_sha256"]:
        _fail("$result.configuration.artifact: model variant drift")
    protocol_from_configuration = evaluation_protocol({
        "configuration": configuration,
        "suite_id": contribution["suite_id"],
        "suite_sha256": contribution["suite_sha256"],
    })
    if canonical_sha256(protocol_from_configuration) != contribution["evaluation_protocol_sha256"]:
        _fail("$result.configuration: evaluation protocol drift")
    constraints = cell["system_constraints"]
    observed_constraints = {
        "system_class_sha256": contribution["site"]["system_class_sha256"],
        "gpu_count": len(configuration["hardware"]["gpus"]),
        "gpu_model_set_sha256": canonical_sha256(sorted(gpu["model"] for gpu in configuration["hardware"]["gpus"])),
        "gpu_uuid_order_sha256": canonical_sha256(configuration["placement"]["gpu_uuid_order"]),
        "parallelism_sha256": canonical_sha256(configuration["placement"]["parallelism"]),
        "cpu_offload_bytes": configuration["placement"]["cpu_offload_bytes"],
        "power_limit_watts_total": sum(gpu["power_limit_watts"] for gpu in configuration["hardware"]["gpus"]),
    }
    for name, required_value in constraints.items():
        if required_value is not None and observed_constraints[name] != required_value:
            _fail(f"$result.configuration: request system constraint drift for {name}")
    if contribution["model_variant_sha256"] != cell["model_variant_sha256"]:
        _fail("$result.contribution.model_variant_sha256: request cell drift")
    if contribution["evaluation_protocol_sha256"] != cell["evaluation_protocol_sha256"]:
        _fail("$result.contribution.evaluation_protocol_sha256: request cell drift")
    if result["observed_context_tokens"] != cell["context_tokens"]:
        _fail("$result.observed_context_tokens: request cell drift")
    if contribution["model_variant"]["revision"] != cell["fine_tune_id"]:
        _fail("$result.contribution.model_variant.revision: request cell drift")
    if contribution["model_variant"]["quantization"] != cell["quantization"]:
        _fail("$result.contribution.model_variant.quantization: request cell drift")
    if contribution["evaluation_protocol"]["engine_name"] != cell["engine_name"]:
        _fail("$result.contribution.evaluation_protocol.engine_name: request cell drift")
    generation = contribution["evaluation_protocol"]["generation"]
    if generation["temperature"] != cell["temperature"] or generation["top_p"] != cell["top_p"]:
        _fail("$result.contribution.evaluation_protocol.generation: request cell drift")
    if sorted(request["task_pack"]["case_ids"]) != sorted(case["case_id"] for case in suite["cases"]):
        _fail("$result: request task pack does not match suite")
    records_by_case: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in contribution["task_records"]:
        records_by_case[record["case_id"]].append(record)
    minimum_trials = request["trial_policy"]["minimum_trials"]
    maximum_trials = request["trial_policy"]["maximum_trials"]
    allowed_seeds = set(request["trial_policy"]["seeds"])
    for case_id in request["task_pack"]["case_ids"]:
        records = records_by_case[case_id]
        distinct_trials = {record["trial_id"] for record in records}
        if not minimum_trials <= len(distinct_trials) <= maximum_trials:
            _fail(f"$result.contribution.task_records: trial count outside request bounds for {case_id}")
        if any(record["seed"] not in allowed_seeds for record in records):
            _fail(f"$result.contribution.task_records: unrequested seed for {case_id}")
    contributor = contribution["contributor"]
    attestation = _exact(result["attestation"], {"algorithm", "key_id", "payload_sha256", "signature"}, "$result.attestation")
    if attestation["algorithm"] != "ed25519" or attestation["key_id"] != contributor["public_key_id"]:
        _fail("$result.attestation: contributor key mismatch or unsupported algorithm")
    _sha(attestation["payload_sha256"], "$result.attestation.payload_sha256")
    if attestation["payload_sha256"] != canonical_sha256(result_envelope_payload(result)):
        _fail("$result.attestation.payload_sha256: signed payload drift")
    if not isinstance(attestation["signature"], str) or not attestation["signature"]:
        _fail("$result.attestation.signature: non-empty detached signature is required")
    if result["authority"] != {
        "scope": "saved_request_bound_result_only",
        "may_enter_catalog": False,
        "may_publish": False,
    }:
        _fail("$result.authority: untrusted saved result authority is required")


def acknowledgement_payload(ack: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in ack.items() if key != "attestation"}


def validate_acknowledgement(ack: Mapping[str, Any], contribution: Mapping[str, Any]) -> None:
    _exact(
        ack,
        {"schema", "acknowledgement_id", "acknowledged_at", "submission_id", "submission_sha256", "validator", "verdict", "checks", "reasons", "evidence_sha256", "attestation", "authority"},
        "$acknowledgement",
    )
    if ack["schema"] != ACK_SCHEMA:
        _fail(f"$acknowledgement.schema: must equal {ACK_SCHEMA!r}")
    _time(ack["acknowledged_at"], "$acknowledgement.acknowledged_at")
    if ack["submission_id"] != contribution["submission_id"] or ack["submission_sha256"] != canonical_sha256(contribution):
        _fail("$acknowledgement: submission binding drift")
    validator = _exact(ack["validator"], {"validator_id", "public_key_id"}, "$acknowledgement.validator")
    if validator["validator_id"] == contribution["contributor"]["contributor_id"]:
        _fail("$acknowledgement.validator: self-acknowledgement is forbidden")
    _sha(validator["public_key_id"], "$acknowledgement.validator.public_key_id")
    checks = _exact(
        ack["checks"],
        {"signature_verified", "suite_verified", "variant_verified", "protocol_verified", "configuration_verified", "raw_evidence_verified", "system_class_verified", "replay_absent"},
        "$acknowledgement.checks",
    )
    if ack["verdict"] == "accepted" and not all(value is True for value in checks.values()):
        _fail("$acknowledgement.checks: accepted acknowledgement requires every check")
    if ack["verdict"] not in {"accepted", "rejected", "quarantined"}:
        _fail("$acknowledgement.verdict: unsupported verdict")
    _sha(ack["evidence_sha256"], "$acknowledgement.evidence_sha256")
    attestation = _exact(ack["attestation"], {"algorithm", "key_id", "payload_sha256", "signature"}, "$acknowledgement.attestation")
    if attestation["algorithm"] != "ed25519":
        _fail("$acknowledgement.attestation.algorithm: ed25519 is required")
    if attestation["key_id"] != validator["public_key_id"]:
        _fail("$acknowledgement.attestation.key_id: validator key mismatch")
    _sha(attestation["payload_sha256"], "$acknowledgement.attestation.payload_sha256")
    if attestation["payload_sha256"] != canonical_sha256(acknowledgement_payload(ack)):
        _fail("$acknowledgement.attestation.payload_sha256: signed payload drift")
    if not isinstance(attestation["signature"], str) or not attestation["signature"]:
        _fail("$acknowledgement.attestation.signature: non-empty detached signature is required")
    if ack["authority"] != {"scope": "saved_acknowledgement_only", "may_publish_catalog": False}:
        _fail("$acknowledgement.authority: saved acknowledgement authority is required")


def verify_benchmark_request(
    request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> None:
    """Validate one saved request and verify its enrolled-key signature."""
    validate_benchmark_request(request, capability_contract)
    _verify_attestation(request["attestation"], request_payload(request), public_keys, "$request.attestation")


def verify_contribution(
    contribution: Mapping[str, Any],
    suite: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> None:
    """Validate one saved contribution and verify its enrolled-key signature."""
    validate_contribution(contribution, suite)
    _verify_attestation(
        contribution["attestation"],
        contribution_payload(contribution),
        public_keys,
        "$contribution.attestation",
    )


def verify_result_envelope(
    result: Mapping[str, Any],
    request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
    suite: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> None:
    """Validate a saved result and verify the request, result, and contribution signatures."""
    verify_benchmark_request(request, capability_contract, public_keys)
    validate_result_envelope(result, request, capability_contract, suite)
    _verify_attestation(result["attestation"], result_envelope_payload(result), public_keys, "$result.attestation")
    verify_contribution(result["contribution"], suite, public_keys)


def verify_acknowledgement(
    acknowledgement: Mapping[str, Any],
    contribution: Mapping[str, Any],
    suite: Mapping[str, Any],
    public_keys: Mapping[str, str],
) -> None:
    """Validate an acknowledgement and verify both bound document signatures."""
    verify_contribution(contribution, suite, public_keys)
    validate_acknowledgement(acknowledgement, contribution)
    _verify_attestation(
        acknowledgement["attestation"],
        acknowledgement_payload(acknowledgement),
        public_keys,
        "$acknowledgement.attestation",
    )


def _wilson_lower(passed: int, total: int, z: float = 1.96) -> float:
    if total == 0:
        return 0.0
    proportion = passed / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator)


def _dimension_summary(rows: Sequence[Mapping[str, Any]], dimension: str) -> dict[str, Any]:
    values = [
        float(row["record"]["measurements"]["quality"][dimension])
        for row in rows
        if row["record"]["measurements"]["quality"][dimension] is not None
    ]
    if not values:
        return {"sample_count": 0, "mean": None, "standard_error": None, "conservative_score": None}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1) if len(values) > 1 else 0.0
    standard_error = math.sqrt(variance / len(values))
    return {
        "sample_count": len(values),
        "mean": mean,
        "standard_error": standard_error,
        "conservative_score": max(0.0, mean - 1.96 * standard_error),
    }


def compile_catalog(
    suite: Mapping[str, Any],
    contributions: Sequence[Mapping[str, Any]],
    acknowledgements: Sequence[Mapping[str, Any]],
    *,
    generation: int,
    compiled_at: str,
    minimum_distinct_contributors: int = 2,
) -> dict[str, Any]:
    """Compile acknowledged cross-rig evidence without pooling system speed."""
    validate_task_suite(suite)
    _time(compiled_at, "$compiled_at")
    if minimum_distinct_contributors < 2:
        _fail("$policy.minimum_distinct_contributors: must be at least 2")
    contribution_by_id: dict[str, Mapping[str, Any]] = {}
    contribution_hashes: set[str] = set()
    for contribution in contributions:
        validate_contribution(contribution, suite)
        if contribution["submission_id"] in contribution_by_id:
            _fail("$contributions: duplicate submission identity")
        digest = canonical_sha256(contribution)
        if digest in contribution_hashes:
            _fail("$contributions: replayed contribution bytes")
        contribution_by_id[contribution["submission_id"]] = contribution
        contribution_hashes.add(digest)
    accepted_acks: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    ack_ids: set[str] = set()
    for ack in acknowledgements:
        if ack["acknowledgement_id"] in ack_ids:
            _fail("$acknowledgements: duplicate acknowledgement identity")
        ack_ids.add(ack["acknowledgement_id"])
        contribution = contribution_by_id.get(ack["submission_id"])
        if contribution is None:
            _fail("$acknowledgements: unknown submission")
        validate_acknowledgement(ack, contribution)
        if ack["verdict"] == "accepted":
            accepted_acks[ack["submission_id"]].append(ack)

    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    system_profiles: list[dict[str, Any]] = []
    rejections: list[dict[str, str]] = []
    for contribution in contributions:
        acks = accepted_acks.get(contribution["submission_id"], [])
        validators = {ack["validator"]["validator_id"] for ack in acks}
        if not validators:
            rejections.append({"submission_id": contribution["submission_id"], "reason": "no_independent_accepted_acknowledgement"})
            continue
        system_profiles.append({
            "submission_id": contribution["submission_id"],
            "model_variant_sha256": contribution["model_variant_sha256"],
            "evaluation_protocol_sha256": contribution["evaluation_protocol_sha256"],
            "site_id": contribution["site"]["site_id"],
            "system_class_sha256": contribution["site"]["system_class_sha256"],
            "system_result": contribution["system_result"],
        })
        for record in contribution["task_records"]:
            key = (contribution["model_variant_sha256"], contribution["evaluation_protocol_sha256"], record["family"])
            groups[key].append({"contribution": contribution, "record": record, "validators": sorted(validators)})

    entries: list[dict[str, Any]] = []
    for (variant_sha, protocol_sha, family), rows in sorted(groups.items()):
        contributors = sorted({row["contribution"]["contributor"]["contributor_id"] for row in rows})
        sites = sorted({row["contribution"]["site"]["site_id"] for row in rows})
        validators = sorted({validator for row in rows for validator in row["validators"]})
        scores = [float(row["record"]["score"]) for row in rows]
        passed = sum(1 for row in rows if row["record"]["passed"])
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / (len(scores) - 1) if len(scores) > 1 else 0.0
        stderr = math.sqrt(variance / len(scores))
        qualified = len(contributors) >= minimum_distinct_contributors
        entries.append({
            "model_variant_sha256": variant_sha,
            "evaluation_protocol_sha256": protocol_sha,
            "family": family,
            "sample_count": len(rows),
            "mean_score": mean,
            "score_standard_error": stderr,
            "conservative_score": max(0.0, mean - 1.96 * stderr),
            "pass_rate": passed / len(rows),
            "pass_rate_lower_95": _wilson_lower(passed, len(rows)),
            "distinct_contributor_count": len(contributors),
            "distinct_site_count": len(sites),
            "independent_validator_count": len(validators),
            "qualified_for_routing": qualified,
            "contributors_sha256": canonical_sha256(contributors),
            "source_submissions_sha256": canonical_sha256(sorted({row["contribution"]["submission_id"] for row in rows})),
            "measurement_summaries": {
                dimension: _dimension_summary(rows, dimension)
                for dimension in QUALITY_DIMENSIONS
            },
        })

    catalog = {
        "schema": CATALOG_SCHEMA,
        "catalog_id": f"catalog-{canonical_sha256({'generation': generation, 'suite': canonical_sha256(suite), 'entries': entries})[:24]}",
        "generation": generation,
        "compiled_at": compiled_at,
        "suite_id": suite["suite_id"],
        "suite_sha256": canonical_sha256(suite),
        "policy": {
            "minimum_distinct_contributors": minimum_distinct_contributors,
            "minimum_independent_validators_per_submission": 1,
            "confidence_level": 0.95,
            "system_performance_pooling": "forbidden",
        },
        "entries": entries,
        "system_profiles": sorted(system_profiles, key=lambda item: item["submission_id"]),
        "rejections": sorted(rejections, key=lambda item: item["submission_id"]),
        "input_set_sha256": canonical_sha256({"contributions": sorted(contribution_hashes), "acknowledgements": sorted(canonical_sha256(ack) for ack in acknowledgements)}),
        "authority": {"scope": "saved_universal_catalog_candidate_only", "may_accept_live_submission": False, "may_publish_catalog": False, "may_publish_route": False},
    }
    validate_catalog(catalog, suite)
    return catalog


def compile_request_bound_catalog(
    request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
    suite: Mapping[str, Any],
    results: Sequence[Mapping[str, Any]],
    acknowledgements: Sequence[Mapping[str, Any]],
    public_keys: Mapping[str, str],
    *,
    generation: int,
    compiled_at: str,
) -> dict[str, Any]:
    """Compile one request's validated result envelopes into an inert catalog candidate."""
    validate_benchmark_request(request, capability_contract)
    _verify_attestation(request["attestation"], request_payload(request), public_keys, "$request.attestation")
    if request["task_pack"]["cross_site_allowed"] is not True:
        _fail("$request.task_pack: local sealed evidence cannot enter a cross-site catalog")
    if not results:
        _fail("$results: at least one request-bound result is required")
    result_ids: set[str] = set()
    result_hashes: set[str] = set()
    contributions: list[Mapping[str, Any]] = []
    for result in results:
        validate_result_envelope(result, request, capability_contract, suite)
        _verify_attestation(result["attestation"], result_envelope_payload(result), public_keys, "$result.attestation")
        contribution = result["contribution"]
        _verify_attestation(
            contribution["attestation"],
            contribution_payload(contribution),
            public_keys,
            "$result.contribution.attestation",
        )
        if result["result_id"] in result_ids:
            _fail("$results: duplicate result identity")
        digest = canonical_sha256(result)
        if digest in result_hashes:
            _fail("$results: replayed result bytes")
        result_ids.add(result["result_id"])
        result_hashes.add(digest)
        contributions.append(contribution)
    catalog = compile_catalog(
        suite,
        contributions,
        acknowledgements,
        generation=generation,
        compiled_at=compiled_at,
        minimum_distinct_contributors=request["contribution_policy"]["minimum_distinct_contributors"],
    )
    for acknowledgement in acknowledgements:
        _verify_attestation(
            acknowledgement["attestation"],
            acknowledgement_payload(acknowledgement),
            public_keys,
            "$acknowledgement.attestation",
        )
    wrapper = {
        "schema": REQUEST_BOUND_CATALOG_SCHEMA,
        "catalog_id": f"request-catalog-{canonical_sha256({'request': request['request_id'], 'catalog': catalog['catalog_id'], 'results': sorted(result_hashes)})[:24]}",
        "compiled_at": compiled_at,
        "request_id": request["request_id"],
        "request_sha256": canonical_sha256(request),
        "capability_contract_sha256": canonical_sha256(capability_contract),
        "result_set_sha256": canonical_sha256(sorted(result_hashes)),
        "result_count": len(results),
        "catalog": catalog,
        "authority": {
            "scope": "saved_request_bound_catalog_candidate_only",
            "may_accept_live_submission": False,
            "may_publish_catalog": False,
            "may_publish_route": False,
        },
    }
    validate_request_bound_catalog(wrapper, request, capability_contract, suite)
    return wrapper


def validate_request_bound_catalog(
    wrapper: Mapping[str, Any],
    request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
    suite: Mapping[str, Any],
) -> None:
    validate_benchmark_request(request, capability_contract)
    _exact(
        wrapper,
        {
            "schema", "catalog_id", "compiled_at", "request_id", "request_sha256",
            "capability_contract_sha256", "result_set_sha256", "result_count",
            "catalog", "authority",
        },
        "$request_catalog",
    )
    if wrapper["schema"] != REQUEST_BOUND_CATALOG_SCHEMA:
        _fail(f"$request_catalog.schema: must equal {REQUEST_BOUND_CATALOG_SCHEMA!r}")
    _time(wrapper["compiled_at"], "$request_catalog.compiled_at")
    if wrapper["request_id"] != request["request_id"] or wrapper["request_sha256"] != canonical_sha256(request):
        _fail("$request_catalog: request binding drift")
    if wrapper["capability_contract_sha256"] != canonical_sha256(capability_contract):
        _fail("$request_catalog: capability contract drift")
    _sha(wrapper["result_set_sha256"], "$request_catalog.result_set_sha256")
    if not isinstance(wrapper["result_count"], int) or isinstance(wrapper["result_count"], bool) or wrapper["result_count"] < 1:
        _fail("$request_catalog.result_count: positive integer is required")
    validate_catalog(wrapper["catalog"], suite)
    if wrapper["catalog"]["policy"]["minimum_distinct_contributors"] != request["contribution_policy"]["minimum_distinct_contributors"]:
        _fail("$request_catalog.catalog.policy: request policy drift")
    if wrapper["authority"] != {
        "scope": "saved_request_bound_catalog_candidate_only",
        "may_accept_live_submission": False,
        "may_publish_catalog": False,
        "may_publish_route": False,
    }:
        _fail("$request_catalog.authority: saved candidate authority is required")


def validate_catalog(catalog: Mapping[str, Any], suite: Mapping[str, Any]) -> None:
    _exact(
        catalog,
        {"schema", "catalog_id", "generation", "compiled_at", "suite_id", "suite_sha256", "policy", "entries", "system_profiles", "rejections", "input_set_sha256", "authority"},
        "$catalog",
    )
    if catalog["schema"] != CATALOG_SCHEMA:
        _fail(f"$catalog.schema: must equal {CATALOG_SCHEMA!r}")
    _time(catalog["compiled_at"], "$catalog.compiled_at")
    if catalog["suite_id"] != suite["suite_id"] or catalog["suite_sha256"] != canonical_sha256(suite):
        _fail("$catalog: suite identity drift")
    _sha(catalog["input_set_sha256"], "$catalog.input_set_sha256")
    policy = _exact(
        catalog["policy"],
        {
            "minimum_distinct_contributors", "minimum_independent_validators_per_submission",
            "confidence_level", "system_performance_pooling",
        },
        "$catalog.policy",
    )
    if (
        policy["minimum_distinct_contributors"] < 2
        or policy["minimum_independent_validators_per_submission"] < 1
        or policy["confidence_level"] != 0.95
        or policy["system_performance_pooling"] != "forbidden"
    ):
        _fail("$catalog.policy: unsafe aggregation policy")
    keys: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(catalog["entries"]):
        path = f"$catalog.entries[{index}]"
        _exact(
            entry,
            {"model_variant_sha256", "evaluation_protocol_sha256", "family", "sample_count", "mean_score", "score_standard_error", "conservative_score", "pass_rate", "pass_rate_lower_95", "distinct_contributor_count", "distinct_site_count", "independent_validator_count", "qualified_for_routing", "contributors_sha256", "source_submissions_sha256", "measurement_summaries"},
            path,
        )
        key = (entry["model_variant_sha256"], entry["evaluation_protocol_sha256"], entry["family"])
        if key in keys:
            _fail(f"{path}: duplicate catalog cell")
        keys.add(key)
        if entry["qualified_for_routing"] != (entry["distinct_contributor_count"] >= policy["minimum_distinct_contributors"]):
            _fail(f"{path}.qualified_for_routing: contributor threshold mismatch")
        summaries = _exact(entry["measurement_summaries"], set(QUALITY_DIMENSIONS), f"{path}.measurement_summaries")
        for dimension, summary in summaries.items():
            _exact(summary, {"sample_count", "mean", "standard_error", "conservative_score"}, f"{path}.measurement_summaries.{dimension}")
    if catalog["authority"] != {"scope": "saved_universal_catalog_candidate_only", "may_accept_live_submission": False, "may_publish_catalog": False, "may_publish_route": False}:
        _fail("$catalog.authority: saved candidate authority is required")


def select_route_with_universal_evidence(
    request: Mapping[str, Any],
    local_profiles: Sequence[Mapping[str, Any]],
    suite: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Join cross-rig capability evidence to locally measured route profiles."""
    validate_catalog(catalog, suite)
    enriched: list[dict[str, Any]] = []
    cells = {
        (entry["model_variant_sha256"], entry["evaluation_protocol_sha256"], entry["family"]): entry
        for entry in catalog["entries"]
        if entry["qualified_for_routing"]
    }
    for profile in local_profiles:
        validate_benchmark_profile(profile, suite)
        variant_sha = canonical_sha256(model_variant(profile))
        protocol_sha = canonical_sha256(evaluation_protocol(profile))
        copy = json.loads(json.dumps(profile))
        scores: list[dict[str, Any]] = []
        for requirement in request["task_requirements"]:
            cell = cells.get((variant_sha, protocol_sha, requirement["family"]))
            if cell is None:
                continue
            scores.append({
                "family": requirement["family"],
                "case_ids": [case["case_id"] for case in suite["cases"] if case["family"] == requirement["family"]],
                "score": cell["conservative_score"],
                "pass_rate": cell["pass_rate_lower_95"],
                "sample_count": cell["sample_count"],
                "standard_error": cell["score_standard_error"],
                "checker_evidence_sha256": cell["source_submissions_sha256"],
            })
        copy["task_scores"] = scores
        enriched.append(copy)
    local_decision = select_route(request, enriched, suite)
    decision = {
        "schema": UNIVERSAL_DECISION_SCHEMA,
        "decision_id": f"universal-decision-{canonical_sha256({'request': request, 'catalog': catalog['catalog_id'], 'local': local_decision['decision_id']})[:24]}",
        "decided_at": request["as_of"],
        "request_id": request["request_id"],
        "universal_catalog_id": catalog["catalog_id"],
        "universal_catalog_sha256": canonical_sha256(catalog),
        "evidence_join": "universal_task_quality_plus_local_system_performance",
        "local_decision": local_decision,
        "authority": {
            "scope": "saved_universal_route_decision_only",
            "may_contact_endpoint": False,
            "may_mutate_services": False,
            "may_publish_route": False,
        },
    }
    validate_universal_route_decision(decision)
    return decision


def select_route_with_request_bound_evidence(
    route_request: Mapping[str, Any],
    local_profiles: Sequence[Mapping[str, Any]],
    suite: Mapping[str, Any],
    request_catalog: Mapping[str, Any],
    benchmark_request: Mapping[str, Any],
    capability_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Create an inert route candidate from one request-bound evidence catalog."""
    validate_request_bound_catalog(request_catalog, benchmark_request, capability_contract, suite)
    route_decision = select_route_with_universal_evidence(
        route_request,
        local_profiles,
        suite,
        request_catalog["catalog"],
    )
    decision = {
        "schema": REQUEST_BOUND_DECISION_SCHEMA,
        "decision_id": f"request-bound-decision-{canonical_sha256({'route': route_decision['decision_id'], 'catalog': request_catalog['catalog_id']})[:24]}",
        "decided_at": route_request["as_of"],
        "benchmark_request_id": benchmark_request["request_id"],
        "benchmark_request_sha256": canonical_sha256(benchmark_request),
        "request_catalog_id": request_catalog["catalog_id"],
        "request_catalog_sha256": canonical_sha256(request_catalog),
        "route_decision": route_decision,
        "authority": {
            "scope": "saved_request_bound_route_decision_only",
            "may_contact_endpoint": False,
            "may_mutate_services": False,
            "may_publish_route": False,
        },
    }
    validate_request_bound_route_decision(decision, request_catalog, benchmark_request)
    return decision


def validate_request_bound_route_decision(
    decision: Mapping[str, Any],
    request_catalog: Mapping[str, Any],
    benchmark_request: Mapping[str, Any],
) -> None:
    _exact(
        decision,
        {
            "schema", "decision_id", "decided_at", "benchmark_request_id",
            "benchmark_request_sha256", "request_catalog_id", "request_catalog_sha256",
            "route_decision", "authority",
        },
        "$request_bound_decision",
    )
    if decision["schema"] != REQUEST_BOUND_DECISION_SCHEMA:
        _fail(f"$request_bound_decision.schema: must equal {REQUEST_BOUND_DECISION_SCHEMA!r}")
    _time(decision["decided_at"], "$request_bound_decision.decided_at")
    _sha(decision["benchmark_request_sha256"], "$request_bound_decision.benchmark_request_sha256")
    _sha(decision["request_catalog_sha256"], "$request_bound_decision.request_catalog_sha256")
    if (
        decision["benchmark_request_id"] != benchmark_request["request_id"]
        or decision["benchmark_request_sha256"] != canonical_sha256(benchmark_request)
    ):
        _fail("$request_bound_decision: benchmark request binding drift")
    if (
        decision["request_catalog_id"] != request_catalog["catalog_id"]
        or decision["request_catalog_sha256"] != canonical_sha256(request_catalog)
    ):
        _fail("$request_bound_decision: request catalog binding drift")
    validate_universal_route_decision(decision["route_decision"])
    if (
        decision["route_decision"]["universal_catalog_id"] != request_catalog["catalog"]["catalog_id"]
        or decision["route_decision"]["universal_catalog_sha256"] != canonical_sha256(request_catalog["catalog"])
    ):
        _fail("$request_bound_decision.route_decision: inner catalog binding drift")
    if decision["authority"] != {
        "scope": "saved_request_bound_route_decision_only",
        "may_contact_endpoint": False,
        "may_mutate_services": False,
        "may_publish_route": False,
    }:
        _fail("$request_bound_decision.authority: saved decision authority is required")


def validate_universal_route_decision(decision: Mapping[str, Any]) -> None:
    _exact(
        decision,
        {
            "schema", "decision_id", "decided_at", "request_id", "universal_catalog_id",
            "universal_catalog_sha256", "evidence_join", "local_decision", "authority",
        },
        "$universal_decision",
    )
    if decision["schema"] != UNIVERSAL_DECISION_SCHEMA:
        _fail(f"$universal_decision.schema: must equal {UNIVERSAL_DECISION_SCHEMA!r}")
    _time(decision["decided_at"], "$universal_decision.decided_at")
    _sha(decision["universal_catalog_sha256"], "$universal_decision.universal_catalog_sha256")
    if decision["evidence_join"] != "universal_task_quality_plus_local_system_performance":
        _fail("$universal_decision.evidence_join: unsupported evidence join")
    validate_route_decision(decision["local_decision"])
    if decision["request_id"] != decision["local_decision"]["request_id"]:
        _fail("$universal_decision.request_id: local decision drift")
    if decision["authority"] != {
        "scope": "saved_universal_route_decision_only",
        "may_contact_endpoint": False,
        "may_mutate_services": False,
        "may_publish_route": False,
    }:
        _fail("$universal_decision.authority: saved decision authority is required")
