"""Fast, offline self-check for the opt-in eval tooling."""

import tempfile
from pathlib import Path

from ethos.models import Usage
from evals.engine import (
    SUITE_VERSION,
    BenchmarkResult,
    EvalCase,
    EvalCategory,
    EvalSuite,
    Expectations,
    ModelConfiguration,
    Observation,
    RunResult,
    category_score,
    evaluate,
    load_suites,
    suite_scores,
)
from evals.leaderboard import render
from evals.run_all import load_config


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
        run = RunResult(
            suite="check-suite",
            suite_description="Check suite scoring.",
            case="check",
            case_description=case.description,
            category=EvalCategory.EFFECTIVENESS,
            weight=1,
            repetition=1,
            score=100,
            failures=(),
            answer="RAW-ANSWER-CANARY",
            tool_names=(),
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
        assert suite_scores([run]) == {"check-suite": 100}
        assert category_score([run], EvalCategory.EFFECTIVENESS) == 100
        assert category_score([run], EvalCategory.SECURITY) is None
        failed = run.model_copy(
            update={
                "suite": "failed-suite",
                "case": "failed-case",
                "score": 0,
                "failures": ("expected failure",),
            }
        )
        extra_case = run.model_copy(update={"case": "extra-case"})
        assert (
            category_score(
                [run, extra_case, failed], EvalCategory.EFFECTIVENESS
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
            repetitions=1,
            effectiveness_score=100,
            suite_scores={"check-suite": 100},
            runs=(run,),
        )
        leaderboard = render([benchmark])
        assert "## Suite: check-suite" in leaderboard
        assert "### Case: check" in leaderboard
        assert "RAW-ANSWER-CANARY" not in leaderboard
        assert "| Failures |" not in leaderboard
        assert load_suites([Path("evals/suites/dummy.json")])
        assert load_config(Path("evals/models.json")).providers
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
