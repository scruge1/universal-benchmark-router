from __future__ import annotations

import copy
import importlib.resources
import json
import tempfile
import unittest
from pathlib import Path

from test_universal_model_router import digest, profile_fixture
from universal_model_router import canonical_sha256, select_route, validate_route_request
from universal_router_cli import build_parser, run
from universal_task_intake import compile_route_request, suggest_answers, validate_answers, validate_questionnaire


def questionnaire_fixture() -> dict:
    return json.loads(importlib.resources.files("router").joinpath("task-intake-questionnaire-v1.json").read_text(encoding="utf-8"))


def suite_fixture() -> dict:
    return json.loads(importlib.resources.files("router").joinpath("standard-task-suite-v1.json").read_text(encoding="utf-8"))


def complete_profile(suite: dict, workload_id: str = "throughput-mid-c8") -> dict:
    profile = profile_fixture(suite, name="intake", gpu_count=4, quality=0.92, goodput=80, latency_ms=900, energy=4)
    workload = next(item for item in suite["workloads"] if item["workload_id"] == workload_id)
    profile["configuration"]["workload"]["workload_id"] = workload_id
    profile["configuration_sha256"] = canonical_sha256(profile["configuration"])
    profile["performance"][0].update({
        "workload_id": workload_id,
        "context_tokens": workload["context_tokens"],
        "output_tokens": workload["output_tokens"],
        "concurrency": workload["concurrency"],
    })
    present = {item["family"] for item in profile["task_scores"]}
    for family in sorted({case["family"] for case in suite["cases"]} - present):
        profile["task_scores"].append({
            "family": family,
            "case_ids": [case["case_id"] for case in suite["cases"] if case["family"] == family],
            "score": 0.92,
            "pass_rate": 0.92,
            "sample_count": 12,
            "standard_error": 0.02,
            "checker_evidence_sha256": digest(f"checker-intake-{family}"),
        })
    profile["capabilities"] = ["chat", "structured_output", "tools"]
    profile["trust_zones"] = ["private", "public"]
    return profile


def answers_fixture(questionnaire: dict, **updates: str) -> dict:
    selected = {
        "job": "solve_math",
        "working_pattern": "repeated_heavy_use",
        "priority": "balanced",
        "data_boundary": "private",
    }
    selected.update(updates)
    return {
        "schema": "task-intake-answers/v1",
        "intake_id": "task-example",
        "question_set_id": questionnaire["question_set_id"],
        "question_set_sha256": canonical_sha256(questionnaire),
        "description": "Solve exact calculations for a private batch job.",
        "answers": selected,
        "confirmed": True,
        "authority": {"scope": "confirmed_task_intake_only", "may_contact_endpoint": False, "may_mutate_services": False},
    }


class TaskIntakeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.questionnaire = questionnaire_fixture()
        self.suite = suite_fixture()

    def test_questionnaire_is_exactly_four_plain_language_questions(self) -> None:
        validate_questionnaire(self.questionnaire)
        self.assertEqual(["job", "working_pattern", "priority", "data_boundary"], [item["id"] for item in self.questionnaire["questions"]])
        labels = " ".join(choice["label"] for question in self.questionnaire["questions"] for choice in question["choices"]).lower()
        for jargon in ("gpu", "token", "quant", "tensor", "temperature"):
            self.assertNotIn(jargon, labels)

    def test_compile_is_deterministic_and_routes_unchanged(self) -> None:
        profile = complete_profile(self.suite)
        answers = answers_fixture(self.questionnaire)
        first = compile_route_request(answers, self.questionnaire, self.suite, [profile], as_of="2026-09-09T10:00:00Z")
        second = compile_route_request(copy.deepcopy(answers), self.questionnaire, self.suite, [copy.deepcopy(profile)], as_of="2026-09-09T10:00:00Z")
        self.assertEqual(first, second)
        validate_route_request(first)
        decision = select_route(first, [profile], self.suite)
        self.assertEqual("selected", decision["outcome"])
        self.assertEqual("route-intake", decision["selected"]["route_id"])

    def test_every_supported_work_pattern_compiles_from_matching_measurement(self) -> None:
        patterns = self.questionnaire["compiler"]["working_patterns"]
        for answer_value, workload_id in patterns.items():
            with self.subTest(answer=answer_value):
                request = compile_route_request(
                    answers_fixture(self.questionnaire, working_pattern=answer_value),
                    self.questionnaire,
                    self.suite,
                    [complete_profile(self.suite, workload_id)],
                    as_of="2026-09-09T10:00:00Z",
                )
                self.assertEqual(workload_id, request["workload_id"])

    def test_every_supported_job_compiles_to_declared_task_families(self) -> None:
        profile = complete_profile(self.suite)
        for answer_value, job in self.questionnaire["compiler"]["jobs"].items():
            with self.subTest(answer=answer_value):
                request = compile_route_request(
                    answers_fixture(self.questionnaire, job=answer_value),
                    self.questionnaire,
                    self.suite,
                    [profile],
                    as_of="2026-09-09T10:00:00Z",
                )
                self.assertEqual([item["family"] for item in job["families"]], [item["family"] for item in request["task_requirements"]])

    def test_description_hints_are_never_confirmation(self) -> None:
        result = suggest_answers("Private Python code with the best quality for one deep task", self.questionnaire)
        self.assertTrue(result["needs_confirmation"])
        self.assertFalse(result["authority"]["may_compile_request"])
        self.assertEqual("write_or_fix_code", result["suggestions"]["job"]["value"])

    def test_uncertain_unsupported_and_unconfirmed_answers_fail(self) -> None:
        for field, value in (("job", "research_or_long_agent_work"), ("priority", "not_sure"), ("job", "unknown")):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "unsupported|unknown"):
                validate_answers(answers_fixture(self.questionnaire, **{field: value}), self.questionnaire)
        value = answers_fixture(self.questionnaire)
        value["confirmed"] = False
        with self.assertRaisesRegex(ValueError, "explicit true"):
            validate_answers(value, self.questionnaire)

    def test_stale_wrong_suite_and_unmeasured_profiles_fail(self) -> None:
        answers = answers_fixture(self.questionnaire)
        stale = complete_profile(self.suite)
        stale["expires_at"] = "2026-09-08T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "stale"):
            compile_route_request(answers, self.questionnaire, self.suite, [stale], as_of="2026-09-09T10:00:00Z")
        unmeasured = complete_profile(self.suite, "latency-short-c1")
        with self.assertRaisesRegex(ValueError, "no current profile"):
            compile_route_request(answers, self.questionnaire, self.suite, [unmeasured], as_of="2026-09-09T10:00:00Z")
        drift = answers_fixture(self.questionnaire)
        drift["question_set_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "identity drift"):
            compile_route_request(drift, self.questionnaire, self.suite, [complete_profile(self.suite)], as_of="2026-09-09T10:00:00Z")
        duplicate = complete_profile(self.suite)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            compile_route_request(answers, self.questionnaire, self.suite, [duplicate, copy.deepcopy(duplicate)], as_of="2026-09-09T10:00:00Z")

    def test_extra_answer_key_fails_closed(self) -> None:
        value = answers_fixture(self.questionnaire)
        value["answers"]["hidden"] = "guess"
        with self.assertRaisesRegex(ValueError, "expected exactly"):
            validate_answers(value, self.questionnaire)

    def test_cli_discovers_suggests_compiles_and_refuses_overwrite(self) -> None:
        profile = complete_profile(self.suite)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            answers_path = root / "answers.json"
            suite_path = root / "suite.json"
            profile_path = root / "profile.json"
            description_path = root / "description.txt"
            output = root / "request.json"
            answers_path.write_text(json.dumps(answers_fixture(self.questionnaire)), encoding="utf-8")
            suite_path.write_text(json.dumps(self.suite), encoding="utf-8")
            profile_path.write_text(json.dumps(profile), encoding="utf-8")
            description_path.write_text("private maths with balanced results", encoding="utf-8")
            discovered = run(build_parser().parse_args(["discover"]))
            self.assertEqual("task-intake-questionnaire/v1", discovered["schema"])
            suggested = run(build_parser().parse_args(["suggest", "--description-file", str(description_path)]))
            self.assertTrue(suggested["needs_confirmation"])
            args = ["compile-request", "--answers", str(answers_path), "--suite", str(suite_path), "--profile", str(profile_path), "--as-of", "2026-09-09T10:00:00Z", "--output", str(output)]
            result = run(build_parser().parse_args(args))
            self.assertEqual("saved", result["result"])
            validate_route_request(json.loads(output.read_text(encoding="utf-8")))
            with self.assertRaisesRegex(ValueError, "output must be absent"):
                run(build_parser().parse_args(args))


if __name__ == "__main__":
    unittest.main()
