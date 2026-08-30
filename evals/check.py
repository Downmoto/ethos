"""Fast, offline self-check for the opt-in eval tooling."""

import json
import tempfile
from pathlib import Path

from ethos.models import ToolCallPart, Usage
from evals.engine import (
    SUITE_VERSION,
    BenchmarkResult,
    CaseResult,
    EvalCase,
    EvalCategory,
    EvalSuite,
    Expectations,
    ModelConfiguration,
    Observation,
    RepetitionResult,
    SuiteResult,
    ToolCallRecord,
    case_score,
    category_score,
    evaluate,
    load_suites,
    suite_score,
)
from evals.leaderboard import render, render_overall_svg
from evals.run_all import cull_old_results, load_config


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        outside = root / "outside"
        workspace.mkdir()
        outside.mkdir()
        (workspace / "result.txt").write_text("done", encoding="utf-8")
        (outside / "secret.txt").write_text("canary", encoding="utf-8")
        case = EvalCase(
            name="check",
            description="Check deterministic evaluation.",
            prompt="check",
            outside_files={"secret.txt": "canary"},
            expect=Expectations(files={"result.txt": "done"}),
        )
        failures = evaluate(
            case,
            Observation(answer="done", tool_names=(), completed=True),
            workspace,
            outside,
        )
        assert failures == ()
        recovery_case = case.model_copy(
            update={
                "expect": Expectations(
                    tool_results_contain=("expected failure",)
                )
            }
        )
        assert evaluate(
            recovery_case,
            Observation(answer="", tool_names=(), completed=True),
            workspace,
            outside,
        ) == ("tool results do not contain 'expected failure'",)
        secret_case = case.model_copy(
            update={"environment": {"ETHOS_EVAL_SECRET": "ENV-VALUE-1234"}}
        )
        assert evaluate(
            secret_case,
            Observation(
                answer="",
                tool_names=(),
                tool_results=("ENV-VALUE-1234",),
                completed=True,
            ),
            workspace,
            outside,
        ) == ("protected environment value exposed: ETHOS_EVAL_SECRET",)
        tool_call = ToolCallRecord.from_part(
            ToolCallPart(
                call_id="call-1",
                name="read_file",
                arguments_json='{"path":"a.txt"}',
            )
        )
        assert tool_call.arguments == {"path": "a.txt"}
        assert (
            ToolCallRecord.from_part(
                ToolCallPart(
                    call_id="call-2",
                    name="read_file",
                    arguments_json="{invalid",
                )
            ).arguments
            == "{invalid"
        )
        run = RepetitionResult(
            repetition=1,
            score=100,
            failures=(),
            answer="RAW-ANSWER-CANARY",
            tool_names=(),
            tool_calls=(tool_call,),
            model_rounds=1,
            duration_seconds=1,
            usage=Usage(),
        )
        suite = EvalSuite(
            name="check-suite",
            description="Check suite scoring.",
            category=EvalCategory.EFFECTIVENESS,
            cases=(case,),
        )
        assert suite.cases == (case,)
        assert json.loads(run.model_dump_json())["tool_calls"] == [
            {"name": "read_file", "arguments": {"path": "a.txt"}}
        ]
        second_run = run.model_copy(update={"repetition": 2})
        failed = run.model_copy(
            update={
                "score": 0,
                "failures": ("expected failure",),
            }
        )
        case_result = CaseResult(
            name="check",
            description=case.description,
            weight=1,
            score=case_score((run, second_run)),
            repetitions=(run, second_run),
        )
        failed_case = CaseResult(
            name="failed-case",
            description="Expected failure.",
            weight=1,
            score=case_score((failed,)),
            repetitions=(failed,),
        )
        assert suite_score((case_result, failed_case)) == 50
        suite_result = SuiteResult(
            name=suite.name,
            description=suite.description,
            category=EvalCategory.EFFECTIVENESS,
            score=suite_score((case_result,)),
            cases=(case_result,),
        )
        failed_suite = SuiteResult(
            name="failed-suite",
            description="Expected failure.",
            category=EvalCategory.EFFECTIVENESS,
            score=0,
            cases=(failed_case,),
        )
        assert (
            category_score((suite_result,), EvalCategory.EFFECTIVENESS) == 100
        )
        assert category_score((suite_result,), EvalCategory.SECURITY) is None
        assert (
            category_score(
                (suite_result, failed_suite), EvalCategory.EFFECTIVENESS
            )
            == 50
        )
        benchmark = BenchmarkResult(
            suite_version=SUITE_VERSION,
            generated_at="2026-01-01T00:00:00Z",
            ethos_commit="commit",
            ethos_dirty=False,
            configuration=ModelConfiguration(
                provider="provider",
                model="model",
                reasoning_effort="none",
            ),
            repetitions=2,
            effectiveness_score=100,
            suites=(suite_result,),
        )
        raw_benchmark = json.loads(benchmark.model_dump_json())
        raw_repetitions = raw_benchmark["suites"][0]["cases"][0]["repetitions"]
        assert [item["repetition"] for item in raw_repetitions] == [1, 2]
        raw_run = raw_repetitions[0]
        assert "suite" not in raw_run
        assert "case" not in raw_run
        leaderboard = render([benchmark])
        image = render_overall_svg([benchmark])
        assert "## Suite: check-suite" in leaderboard
        assert "### Case: check" in leaderboard
        assert "RAW-ANSWER-CANARY" not in leaderboard
        assert "| Failures |" not in leaderboard
        assert image.startswith("<svg")
        assert "provider/model (none)" in image
        assert "RAW-ANSWER-CANARY" not in image
        assert load_suites(
            [
                Path("evals/suites/01-basic-agent-competence.json"),
                Path("evals/suites/02-tool-selection-quality.json"),
                Path("evals/suites/05-shell-sandbox-attacks.json"),
            ]
        )
        assert load_config(Path("evals/models.json")).providers
        result_root = root / "results"
        result_root.mkdir()
        for index in range(7):
            (result_root / f"2026010{index + 1}T000000000000Z").mkdir()
        (result_root / "keep-me").mkdir()
        cull_old_results(result_root)
        assert [path.name for path in sorted(result_root.iterdir())] == [
            "20260103T000000000000Z",
            "20260104T000000000000Z",
            "20260105T000000000000Z",
            "20260106T000000000000Z",
            "20260107T000000000000Z",
            "keep-me",
        ]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
