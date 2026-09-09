"""Plain-language task intake compiled to an inert route request."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

from universal_model_router import (
    canonical_sha256,
    validate_benchmark_profile,
    validate_route_request,
    validate_task_suite,
)


QUESTIONNAIRE_SCHEMA = "task-intake-questionnaire/v1"
ANSWERS_SCHEMA = "task-intake-answers/v1"
QUESTION_IDS = ("job", "working_pattern", "priority", "data_boundary")
QUESTION_AUTHORITY = {
    "scope": "offline_task_intake_questions_only",
    "may_infer_confirmation": False,
    "may_contact_endpoint": False,
}
ANSWER_AUTHORITY = {
    "scope": "confirmed_task_intake_only",
    "may_contact_endpoint": False,
    "may_mutate_services": False,
}
REQUEST_AUTHORITY = {
    "scope": "offline_route_selection_only",
    "may_contact_endpoint": False,
    "may_mutate_services": False,
}

_HINT_TERMS = {
    "job": {
        "write_or_fix_code": ("code", "coding", "python", "javascript", "bug", "repository", "patch"),
        "solve_math": ("math", "maths", "calculate", "equation", "arithmetic"),
        "reason_through_problem": ("reason", "reasoning", "logic", "plan", "analyse", "analyze"),
        "use_tools_and_recover": ("tool", "tools", "api", "terminal", "retry", "recover"),
        "produce_exact_output": ("json", "schema", "structured", "exact format", "sql"),
        "research_or_long_agent_work": ("research", "long horizon", "multi-agent", "multi agent"),
    },
    "working_pattern": {
        "quick_interactive": ("quick", "short", "interactive", "one at a time"),
        "deep_single_task": ("deep", "difficult", "single task", "one task"),
        "several_tasks": ("several", "parallel", "small team"),
        "repeated_heavy_use": ("steady", "throughput", "many requests", "heavy use"),
        "large_input": ("large input", "long context", "large document", "many files"),
    },
    "priority": {
        "quality": ("best", "quality", "accurate", "accuracy"),
        "fast_response": ("fast", "latency", "responsive"),
        "more_work": ("throughput", "more work", "many users"),
        "lower_resource_use": ("efficient", "efficiency", "power", "cheap", "resource"),
        "balanced": ("balanced", "balance"),
    },
    "data_boundary": {
        "private": ("private", "local", "sensitive", "offline"),
        "public": ("public", "non-sensitive", "non sensitive", "remote allowed"),
    },
}


def _exact(value: Mapping[str, Any], keys: set[str], path: str) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{path}: expected exactly {sorted(keys)!r}")


def _time(value: str, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{path}: UTC date-time ending in Z is required")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{path}: invalid date-time") from exc


def _choices(questionnaire: Mapping[str, Any]) -> dict[str, dict[str, Mapping[str, Any]]]:
    return {
        question["id"]: {choice["value"]: choice for choice in question["choices"]}
        for question in questionnaire["questions"]
    }


def validate_questionnaire(questionnaire: Mapping[str, Any]) -> None:
    _exact(questionnaire, {"schema", "question_set_id", "version", "questions", "compiler", "authority"}, "$questionnaire")
    if questionnaire["schema"] != QUESTIONNAIRE_SCHEMA or questionnaire["authority"] != QUESTION_AUTHORITY:
        raise ValueError("$questionnaire: unsupported schema or authority")
    if not isinstance(questionnaire["question_set_id"], str) or not questionnaire["question_set_id"]:
        raise ValueError("$questionnaire.question_set_id: non-empty string required")
    if not isinstance(questionnaire["version"], str) or not questionnaire["version"]:
        raise ValueError("$questionnaire.version: non-empty string required")
    questions = questionnaire["questions"]
    if not isinstance(questions, list) or len(questions) != 4:
        raise ValueError("$questionnaire.questions: exactly four questions required")
    if tuple(question["id"] for question in questions) != QUESTION_IDS:
        raise ValueError("$questionnaire.questions: stable question order is required")
    for index, question in enumerate(questions):
        _exact(question, {"id", "prompt", "why", "choices"}, f"$questionnaire.questions[{index}]")
        if not isinstance(question["prompt"], str) or not isinstance(question["why"], str) or not question["prompt"] or not question["why"] or not question["choices"]:
            raise ValueError(f"$questionnaire.questions[{index}]: prompt, reason, and choices are required")
        values: set[str] = set()
        for choice_index, choice in enumerate(question["choices"]):
            _exact(choice, {"value", "label", "supported"}, f"$questionnaire.questions[{index}].choices[{choice_index}]")
            if not isinstance(choice["value"], str) or not choice["value"] or not isinstance(choice["label"], str) or not choice["label"]:
                raise ValueError(f"$questionnaire.questions[{index}].choices: non-empty value and label required")
            if choice["value"] in values or not isinstance(choice["supported"], bool):
                raise ValueError(f"$questionnaire.questions[{index}].choices: duplicate or invalid choice")
            values.add(choice["value"])
    compiler = questionnaire["compiler"]
    _exact(compiler, {"jobs", "working_patterns", "priorities", "data_boundaries"}, "$questionnaire.compiler")
    choices = _choices(questionnaire)
    for question_id, mapping_key in (("job", "jobs"), ("working_pattern", "working_patterns"), ("priority", "priorities"), ("data_boundary", "data_boundaries")):
        supported = {value for value, choice in choices[question_id].items() if choice["supported"]}
        if set(compiler[mapping_key]) != supported:
            raise ValueError(f"$questionnaire.compiler.{mapping_key}: must map every supported choice exactly")
    for value, job in compiler["jobs"].items():
        _exact(job, {"families", "capabilities"}, f"$questionnaire.compiler.jobs.{value}")
        if not isinstance(job["families"], list) or not job["families"] or not isinstance(job["capabilities"], list) or not job["capabilities"]:
            raise ValueError(f"$questionnaire.compiler.jobs.{value}: families and capabilities are required")
        seen_families: set[str] = set()
        for index, family in enumerate(job["families"]):
            _exact(family, {"family", "weight"}, f"$questionnaire.compiler.jobs.{value}.families[{index}]")
            if not isinstance(family["family"], str) or not family["family"] or family["family"] in seen_families or not isinstance(family["weight"], int) or family["weight"] < 1:
                raise ValueError(f"$questionnaire.compiler.jobs.{value}.families: unique names and positive integer weights required")
            seen_families.add(family["family"])
        if any(not isinstance(item, str) or not item for item in job["capabilities"]) or len(set(job["capabilities"])) != len(job["capabilities"]):
            raise ValueError(f"$questionnaire.compiler.jobs.{value}.capabilities: unique non-empty strings required")
    if any(not isinstance(value, str) or not value for value in compiler["working_patterns"].values()):
        raise ValueError("$questionnaire.compiler.working_patterns: non-empty workload IDs required")
    for value, priority in compiler["priorities"].items():
        _exact(priority, {"minimum_score", "minimum_pass_rate", "minimum_samples", "objective_order"}, f"$questionnaire.compiler.priorities.{value}")
        if not 0 <= priority["minimum_score"] <= 1 or not 0 <= priority["minimum_pass_rate"] <= 1 or not isinstance(priority["minimum_samples"], int) or priority["minimum_samples"] < 1:
            raise ValueError(f"$questionnaire.compiler.priorities.{value}: invalid evidence floor")
        if not isinstance(priority["objective_order"], list) or not priority["objective_order"] or len(set(priority["objective_order"])) != len(priority["objective_order"]):
            raise ValueError(f"$questionnaire.compiler.priorities.{value}.objective_order: unique non-empty list required")
    if any(not isinstance(value, str) or not value for value in compiler["data_boundaries"].values()):
        raise ValueError("$questionnaire.compiler.data_boundaries: non-empty trust zones required")


def validate_answers(answers: Mapping[str, Any], questionnaire: Mapping[str, Any]) -> None:
    validate_questionnaire(questionnaire)
    _exact(answers, {"schema", "intake_id", "question_set_id", "question_set_sha256", "description", "answers", "confirmed", "authority"}, "$answers")
    if answers["schema"] != ANSWERS_SCHEMA or answers["authority"] != ANSWER_AUTHORITY:
        raise ValueError("$answers: unsupported schema or authority")
    if not isinstance(answers["intake_id"], str) or not answers["intake_id"]:
        raise ValueError("$answers.intake_id: non-empty string required")
    if not isinstance(answers["description"], str) or len(answers["description"]) > 4000:
        raise ValueError("$answers.description: string of at most 4000 characters required")
    if answers["question_set_id"] != questionnaire["question_set_id"] or answers["question_set_sha256"] != canonical_sha256(questionnaire):
        raise ValueError("$answers: question set identity drift")
    if answers["confirmed"] is not True:
        raise ValueError("$answers.confirmed: explicit true is required")
    _exact(answers["answers"], set(QUESTION_IDS), "$answers.answers")
    choices = _choices(questionnaire)
    for question_id in QUESTION_IDS:
        value = answers["answers"][question_id]
        choice = choices[question_id].get(value)
        if choice is None:
            raise ValueError(f"$answers.answers.{question_id}: unknown choice")
        if not choice["supported"]:
            raise ValueError(f"$answers.answers.{question_id}: unsupported or uncertain choice {value!r}")


def suggest_answers(description: str, questionnaire: Mapping[str, Any]) -> dict[str, Any]:
    validate_questionnaire(questionnaire)
    if not isinstance(description, str) or not description.strip() or len(description) > 4000:
        raise ValueError("$description: 1 to 4000 characters required")
    normalized = description.casefold()
    suggestions: dict[str, Any] = {}
    unresolved: list[str] = []
    for question_id in QUESTION_IDS:
        scored: list[tuple[int, str, list[str]]] = []
        for value, terms in _HINT_TERMS[question_id].items():
            matches = sorted(term for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", normalized))
            if matches:
                scored.append((len(matches), value, matches))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if scored and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            suggestions[question_id] = {"value": scored[0][1], "matched_terms": scored[0][2]}
        else:
            unresolved.append(question_id)
    return {
        "schema": "task-intake-suggestions/v1",
        "question_set_id": questionnaire["question_set_id"],
        "question_set_sha256": canonical_sha256(questionnaire),
        "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        "suggestions": suggestions,
        "unresolved_questions": unresolved,
        "needs_confirmation": True,
        "authority": {"scope": "unconfirmed_task_intake_suggestions_only", "may_compile_request": False},
    }


def compile_route_request(
    answers: Mapping[str, Any],
    questionnaire: Mapping[str, Any],
    suite: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    *,
    as_of: str,
) -> dict[str, Any]:
    validate_answers(answers, questionnaire)
    validate_task_suite(suite)
    as_of_time = _time(as_of, "$as_of")
    if not profiles:
        raise ValueError("$profiles: at least one saved profile is required")
    compiler = questionnaire["compiler"]
    selected = answers["answers"]
    job = compiler["jobs"][selected["job"]]
    pattern = compiler["working_patterns"][selected["working_pattern"]]
    priority = compiler["priorities"][selected["priority"]]
    trust_zone = compiler["data_boundaries"][selected["data_boundary"]]
    families = {case["family"] for case in suite["cases"]}
    missing_families = sorted(item["family"] for item in job["families"] if item["family"] not in families)
    if missing_families:
        raise ValueError(f"$answers.answers.job: suite does not measure {missing_families!r}")
    workload = next((item for item in suite["workloads"] if item["workload_id"] == pattern), None)
    if workload is None:
        raise ValueError("$answers.answers.working_pattern: suite does not contain the selected workload")
    applicable: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    profile_ids: set[str] = set()
    route_ids: set[str] = set()
    for index, profile in enumerate(profiles):
        validate_benchmark_profile(profile, suite)
        if profile["profile_id"] in profile_ids or profile["route"]["route_id"] in route_ids:
            raise ValueError(f"$profiles[{index}]: duplicate profile or route identity")
        profile_ids.add(profile["profile_id"])
        route_ids.add(profile["route"]["route_id"])
        if _time(profile["expires_at"], f"$profiles[{index}].expires_at") < as_of_time:
            raise ValueError(f"$profiles[{index}]: stale saved profile")
        if trust_zone not in profile["trust_zones"]:
            continue
        measured = next((item for item in profile["performance"] if item["workload_id"] == pattern), None)
        if measured is not None:
            applicable.append((profile, measured))
    if not applicable:
        raise ValueError("$profiles: no current profile measures the selected workload in the selected data boundary")
    resources = [profile["resource_cost"] for profile, _ in applicable]
    reliability = [profile["reliability"] for profile, _ in applicable]
    performance = [measured for _, measured in applicable]
    route_request = {
        "schema": "model-route-request/v1",
        "request_id": f"{answers['intake_id']}-{canonical_sha256(answers)[:16]}",
        "as_of": as_of,
        "suite_id": suite["suite_id"],
        "suite_sha256": canonical_sha256(suite),
        "task_requirements": [
            {
                "family": item["family"],
                "minimum_score": priority["minimum_score"],
                "minimum_pass_rate": priority["minimum_pass_rate"],
                "minimum_samples": priority["minimum_samples"],
                "weight": item["weight"],
            }
            for item in job["families"]
        ],
        "capabilities": job["capabilities"],
        "workload_id": pattern,
        "context_tokens": workload["context_tokens"],
        "output_tokens": workload["output_tokens"],
        "concurrency": workload["concurrency"],
        "trust_zone": trust_zone,
        "allowed_scale_axes": sorted({profile["scaling"]["axis"] for profile, _ in applicable}),
        "limits": {
            "max_e2e_p95_ms": max(item["e2e_p95_ms"] for item in performance),
            "max_energy_joules_per_output_token": max(item["energy_joules_per_output_token"] for item in performance),
            "max_gpu_count": max(item["gpu_count"] for item in resources),
            "max_host_ram_bytes": max(item["host_ram_bytes"] for item in resources),
            "minimum_request_success_ratio": min(item["request_success_ratio"] for item in reliability),
            "require_restoration": True,
        },
        "objective_order": priority["objective_order"],
        "authority": REQUEST_AUTHORITY,
    }
    validate_route_request(route_request)
    return route_request
