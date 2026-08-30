"""Build a detailed Markdown leaderboard from Ethos benchmark results."""

import argparse
from pathlib import Path

from evals.engine import BenchmarkResult, RunResult


def _score(value: float | None, maximum: int = 100) -> str:
    return "—" if value is None else f"{value:.2f}/{maximum}"


def _average(values: list[float], digits: int = 2) -> str:
    return "—" if not values else f"{sum(values) / len(values):.{digits}f}"


def _label(benchmark: BenchmarkResult) -> str:
    config = benchmark.configuration
    return f"{config.provider}/{config.model} ({config.reasoning_effort})"


def _comparison_row(
    benchmark: BenchmarkResult,
    runs: list[RunResult],
    score: float | None,
) -> str:
    costs = [
        run.estimated_cost_usd
        for run in runs
        if run.estimated_cost_usd is not None
    ]
    return (
        f"| {_label(benchmark)} | {_score(score)} "
        f"| {sum(run.score == 100 for run in runs)}/{len(runs)} "
        f"| {_average([float(len(run.tool_names)) for run in runs])} "
        f"| {_average([float(run.model_rounds) for run in runs])} "
        f"| {_average([float(run.usage.total_tokens) for run in runs])} "
        f"| {_average([run.duration_seconds for run in runs])} "
        f"| {_average(costs, 6)} |"
    )


def render(benchmarks: list[BenchmarkResult]) -> str:
    benchmarks.sort(
        key=lambda item: (
            item.combined_score
            if item.combined_score is not None
            else item.effectiveness_score or -1
        ),
        reverse=True,
    )
    rows = [
        "# Ethos model leaderboard",
        "",
        "Answers are retained only in the raw JSON result files.",
        "",
        "## Overall",
        "",
        "| Model configuration | Effectiveness | Security | Combined "
        "| Runs | Avg seconds | Avg cost (USD) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in benchmarks:
        costs = [
            run.estimated_cost_usd
            for run in item.runs
            if run.estimated_cost_usd is not None
        ]
        rows.append(
            f"| {_label(item)} "
            f"| {_score(item.effectiveness_score)} "
            f"| {_score(item.security_score)} "
            f"| {_score(item.combined_score, 200)} "
            f"| {len(item.runs)} "
            f"| {_average([run.duration_seconds for run in item.runs])} "
            f"| {_average(costs, 6)} |"
        )

    suite_metadata: dict[str, tuple[str, str]] = {}
    for benchmark in benchmarks:
        for run in benchmark.runs:
            suite_metadata.setdefault(
                run.suite,
                (run.suite_description, run.category.value),
            )
    for suite, (description, category) in suite_metadata.items():
        rows.extend(
            (
                "",
                f"## Suite: {suite}",
                "",
                description,
                "",
                f"Category: **{category.title()}**",
                "",
                "| Model configuration | Score | Passed runs | Avg tool calls "
                "| Avg rounds | Avg tokens | Avg seconds | Avg cost (USD) |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            )
        )
        for benchmark in benchmarks:
            suite_runs = [run for run in benchmark.runs if run.suite == suite]
            if suite_runs:
                rows.append(
                    _comparison_row(
                        benchmark,
                        suite_runs,
                        benchmark.suite_scores[suite],
                    )
                )

        case_metadata: dict[str, str] = {}
        for benchmark in benchmarks:
            for run in benchmark.runs:
                if run.suite == suite:
                    case_metadata.setdefault(run.case, run.case_description)
        for case, case_description in case_metadata.items():
            rows.extend(
                (
                    "",
                    f"### Case: {case}",
                    "",
                    case_description,
                    "",
                    "| Model configuration | Score | Passed runs "
                    "| Avg tool calls | Avg rounds | Avg tokens | Avg seconds "
                    "| Avg cost (USD) |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                )
            )
            for benchmark in benchmarks:
                case_runs = [
                    run
                    for run in benchmark.runs
                    if run.suite == suite and run.case == case
                ]
                if case_runs:
                    score = sum(run.score for run in case_runs) / len(case_runs)
                    rows.append(_comparison_row(benchmark, case_runs, score))
    return "\n".join(rows) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    benchmarks = [
        BenchmarkResult.model_validate_json(path.read_text(encoding="utf-8"))
        for path in args.results
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(benchmarks), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
