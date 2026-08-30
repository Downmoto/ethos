"""Build a detailed Markdown leaderboard from Ethos benchmark results."""

import argparse
from html import escape
from pathlib import Path

from evals.engine import BenchmarkResult, RepetitionResult, SuiteResult


def _score(value: float | None, maximum: int = 100) -> str:
    return "—" if value is None else f"{value:.2f}/{maximum}"


def _average(values: list[float], digits: int = 2) -> str:
    return "—" if not values else f"{sum(values) / len(values):.{digits}f}"


def _label(benchmark: BenchmarkResult) -> str:
    config = benchmark.configuration
    return f"{config.provider}/{config.model} ({config.reasoning_effort})"


def _comparison_row(
    benchmark: BenchmarkResult,
    runs: list[RepetitionResult],
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


def _all_repetitions(benchmark: BenchmarkResult) -> list[RepetitionResult]:
    return [
        repetition
        for suite in benchmark.suites
        for case in suite.cases
        for repetition in case.repetitions
    ]


def _suite(benchmark: BenchmarkResult, name: str) -> SuiteResult | None:
    return next(
        (suite for suite in benchmark.suites if suite.name == name), None
    )


def _ranking(benchmarks: list[BenchmarkResult]) -> list[BenchmarkResult]:
    return sorted(
        benchmarks,
        key=lambda item: (
            item.combined_score
            if item.combined_score is not None
            else item.effectiveness_score or -1
        ),
        reverse=True,
    )


def render(benchmarks: list[BenchmarkResult]) -> str:
    benchmarks = _ranking(benchmarks)
    rows = [
        "# Ethos model leaderboard",
        "",
        "Answers are retained only in the raw JSON result files.",
        "",
        "## Overall",
        "",
        "| Model configuration | Effectiveness | Security | Combined "
        "| Repetitions | Avg seconds | Avg cost (USD) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in benchmarks:
        repetitions = _all_repetitions(item)
        costs = [
            run.estimated_cost_usd
            for run in repetitions
            if run.estimated_cost_usd is not None
        ]
        rows.append(
            f"| {_label(item)} "
            f"| {_score(item.effectiveness_score)} "
            f"| {_score(item.security_score)} "
            f"| {_score(item.combined_score, 200)} "
            f"| {len(repetitions)} "
            f"| {_average([run.duration_seconds for run in repetitions])} "
            f"| {_average(costs, 6)} |"
        )

    suite_metadata: dict[str, SuiteResult] = {}
    for benchmark in benchmarks:
        for suite_result in benchmark.suites:
            suite_metadata.setdefault(suite_result.name, suite_result)
    for suite_name, metadata in suite_metadata.items():
        rows.extend(
            (
                "",
                f"## Suite: {suite_name}",
                "",
                metadata.description,
                "",
                f"Category: **{metadata.category.value.title()}**",
                "",
                "| Model configuration | Score | Passed repetitions "
                "| Avg tool calls "
                "| Avg rounds | Avg tokens | Avg seconds | Avg cost (USD) |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            )
        )
        for benchmark in benchmarks:
            benchmark_suite = _suite(benchmark, suite_name)
            if benchmark_suite is not None:
                suite_runs = [
                    repetition
                    for suite_case in benchmark_suite.cases
                    for repetition in suite_case.repetitions
                ]
                rows.append(
                    _comparison_row(
                        benchmark, suite_runs, benchmark_suite.score
                    )
                )

        case_metadata: dict[str, str] = {}
        for benchmark in benchmarks:
            benchmark_suite = _suite(benchmark, suite_name)
            if benchmark_suite is not None:
                for suite_case in benchmark_suite.cases:
                    case_metadata.setdefault(
                        suite_case.name, suite_case.description
                    )
        for case_name, case_description in case_metadata.items():
            rows.extend(
                (
                    "",
                    f"### Case: {case_name}",
                    "",
                    case_description,
                    "",
                    "| Model configuration | Score | Passed repetitions "
                    "| Avg tool calls | Avg rounds | Avg tokens | Avg seconds "
                    "| Avg cost (USD) |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
                )
            )
            for benchmark in benchmarks:
                benchmark_suite = _suite(benchmark, suite_name)
                if benchmark_suite is None:
                    continue
                case_result = next(
                    (
                        item
                        for item in benchmark_suite.cases
                        if item.name == case_name
                    ),
                    None,
                )
                if case_result is not None:
                    rows.append(
                        _comparison_row(
                            benchmark,
                            list(case_result.repetitions),
                            case_result.score,
                        )
                    )
    return "\n".join(rows) + "\n"


def render_overall_svg(benchmarks: list[BenchmarkResult]) -> str:
    width = 1080
    row_height = 50
    table_top = 84
    header_height = 38
    height = table_top + header_height + len(benchmarks) * row_height + 24
    rows: list[str] = []
    for index, benchmark in enumerate(_ranking(benchmarks), start=1):
        top = table_top + header_height + (index - 1) * row_height
        baseline = top + 31
        fill = "#161b22" if index % 2 else "#0d1117"
        accent = (
            f'<rect x="28" y="{top}" width="4" height="{row_height}" '
            'fill="#2f81f7"/>'
            if index == 1
            else ""
        )
        rows.extend(
            (
                f'<rect x="28" y="{top}" width="1024" '
                f'height="{row_height}" fill="{fill}"/>{accent}',
                f'<text x="48" y="{baseline}" class="rank">{index}</text>',
                f'<text x="100" y="{baseline}" class="model">'
                f"{escape(_label(benchmark))}</text>",
                f'<text x="640" y="{baseline}" class="score">'
                f"{_score(benchmark.effectiveness_score)}</text>",
                f'<text x="800" y="{baseline}" class="score">'
                f"{_score(benchmark.security_score)}</text>",
                f'<text x="940" y="{baseline}" class="score combined">'
                f"{_score(benchmark.combined_score, 200)}</text>",
            )
        )
    return "\n".join(
        (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="title description">',
            '<title id="title">Ethos model leaderboard</title>',
            '<desc id="description">Latest overall model evaluation '
            "scores.</desc>",
            '<rect width="100%" height="100%" rx="16" fill="#0d1117"/>',
            "<style>",
            "text { font-family: -apple-system, BlinkMacSystemFont, "
            "'Segoe UI', sans-serif; fill: #f0f6fc; }",
            ".title { font-size: 28px; font-weight: 700; }",
            ".subtitle, .header { fill: #8b949e; }",
            ".subtitle { font-size: 14px; }",
            ".header { font-size: 13px; font-weight: 600; }",
            ".rank, .model, .score { font-size: 16px; }",
            ".model, .combined { font-weight: 600; }",
            "</style>",
            '<text x="32" y="42" class="title">Ethos model leaderboard</text>',
            '<text x="32" y="67" class="subtitle">Latest overall '
            "results</text>",
            f'<rect x="28" y="{table_top}" width="1024" '
            f'height="{header_height}" rx="8" fill="#21262d"/>',
            '<text x="48" y="109" class="header">RANK</text>',
            '<text x="100" y="109" class="header">MODEL</text>',
            '<text x="640" y="109" class="header">EFFECTIVENESS</text>',
            '<text x="800" y="109" class="header">SECURITY</text>',
            '<text x="940" y="109" class="header">COMBINED</text>',
            *rows,
            "</svg>",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    benchmarks = [
        BenchmarkResult.model_validate_json(path.read_text(encoding="utf-8"))
        for path in args.results
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(benchmarks), encoding="utf-8")
    if args.image is not None:
        args.image.parent.mkdir(parents=True, exist_ok=True)
        args.image.write_text(render_overall_svg(benchmarks), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
