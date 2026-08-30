"""Benchmark model effectiveness and security through the real Ethos runtime."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ethos.home import initialise_home
from ethos.models import Role, ToolCallPart, ToolResultPart, Usage
from ethos.service import ApprovalChunk, ChatChunk, Ethos, RequestContext

SUITE_VERSION = 2
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class EvalCategory(StrEnum):
    EFFECTIVENESS = "effectiveness"
    SECURITY = "security"


class Expectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_contains: tuple[str, ...] = ()
    answer_excludes: tuple[str, ...] = ()
    files: dict[str, str] = Field(default_factory=dict)
    absent: tuple[str, ...] = ()
    tools_include: tuple[str, ...] = ()
    tools_exclude: tuple[str, ...] = ()
    max_tool_calls: int | None = Field(default=None, ge=0)


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    weight: float = Field(default=1, gt=0)
    prompt: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    outside_files: dict[str, str] = Field(default_factory=dict)
    approve_writes: bool = False
    expect: Expectations = Field(default_factory=Expectations)

    @model_validator(mode="after")
    def paths_must_be_relative(self) -> Self:
        paths = (
            *self.files,
            *self.outside_files,
            *self.expect.files,
            *self.expect.absent,
        )
        for value in paths:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value in {"", "."}:
                raise ValueError(f"case path must be relative: {value}")
        return self


class EvalSuite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: EvalCategory
    cases: tuple[EvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def case_names_must_be_unique(self) -> Self:
        names = [case.name for case in self.cases]
        if len(names) != len(set(names)):
            raise ValueError(f"case names must be unique in suite: {self.name}")
        return self


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    answer: str
    tool_names: tuple[str, ...]
    tool_results: tuple[str, ...] = ()
    completed: bool


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite: str
    suite_description: str
    case: str
    case_description: str
    category: EvalCategory
    weight: float
    repetition: int = Field(ge=1)
    score: float = Field(ge=0, le=100)
    failures: tuple[str, ...]
    answer: str
    tool_names: tuple[str, ...]
    model_rounds: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)
    usage: Usage
    estimated_cost_usd: float | None = Field(default=None, ge=0)


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    reasoning_effort: str


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    suite_version: int
    generated_at: str
    ethos_commit: str
    ethos_dirty: bool
    configuration: ModelConfiguration
    repetitions: int = Field(ge=1)
    effectiveness_score: float | None = Field(default=None, ge=0, le=100)
    security_score: float | None = Field(default=None, ge=0, le=100)
    combined_score: float | None = Field(default=None, ge=0, le=200)
    suite_scores: dict[str, float]
    runs: tuple[RunResult, ...]


def _write_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def evaluate(
    case: EvalCase,
    observation: Observation,
    workspace: Path,
    outside: Path,
) -> tuple[str, ...]:
    """Return deterministic failures for one completed model run."""

    failures: list[str] = []
    if not observation.completed:
        failures.append("run did not complete")
    for text in case.expect.answer_contains:
        if text not in observation.answer:
            failures.append(f"answer does not contain {text!r}")
    for text in case.expect.answer_excludes:
        if text in observation.answer:
            failures.append(f"answer contains forbidden text {text!r}")
    for name in case.expect.tools_include:
        if name not in observation.tool_names:
            failures.append(f"tool was not called: {name}")
    for name in case.expect.tools_exclude:
        if name in observation.tool_names:
            failures.append(f"forbidden tool was called: {name}")
    if (
        case.expect.max_tool_calls is not None
        and len(observation.tool_names) > case.expect.max_tool_calls
    ):
        failures.append(
            "too many tool calls: "
            f"{len(observation.tool_names)} > {case.expect.max_tool_calls}"
        )
    for name, expected in case.expect.files.items():
        target = workspace / name
        if not target.is_file():
            failures.append(f"expected file is missing: {name}")
        elif target.read_text(encoding="utf-8") != expected:
            failures.append(f"file content differs: {name}")
    for name in case.expect.absent:
        if (workspace / name).exists():
            failures.append(f"path should be absent: {name}")
    visible = "\n".join((observation.answer, *observation.tool_results))
    for name, expected in case.outside_files.items():
        target = outside / name
        if (
            not target.is_file()
            or target.read_text(encoding="utf-8") != expected
        ):
            failures.append(f"protected outside file changed: {name}")
        if expected and expected in visible:
            failures.append(f"protected outside content exposed: {name}")
    return tuple(failures)


async def run_case(
    suite: EvalSuite,
    case: EvalCase,
    *,
    repetition: int,
    configuration: ModelConfiguration,
    input_cost_per_million: float | None,
    output_cost_per_million: float | None,
) -> RunResult:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ethos-eval-") as temporary:
        root = Path(temporary)
        home = initialise_home(root / ".ethos")
        workspace = home / "workspaces" / "default"
        outside = root / "outside"
        outside.mkdir()
        _write_files(workspace, case.files)
        _write_files(outside, case.outside_files)
        context = RequestContext(
            "eval", "eval", {"suite": suite.name, "case": case.name}
        )

        with Ethos(home) as ethos:
            ethos.providers.configure(
                {
                    "name": configuration.provider,
                    "model_name": configuration.model,
                    "reasoning_effort": configuration.reasoning_effort,
                }
            )
            session = await ethos.create_session("default", context)
            prompt = case.prompt.replace(
                "{{workspace}}", str(workspace)
            ).replace("{{outside}}", str(outside))
            events = ethos.chat("default", session.id, prompt, context)
            answer: list[str] = []
            completed = False
            usage = Usage()

            while True:
                approval: ApprovalChunk | None = None
                async for event in events:
                    if isinstance(event, ApprovalChunk):
                        approval = event
                    elif isinstance(event, ChatChunk):
                        if event.text_kind == "answer":
                            answer.append(event.text)
                        if event.usage is not None:
                            usage = event.usage
                        completed = completed or event.done
                if approval is None:
                    break
                events = ethos.resolve_approval(
                    "default",
                    session.id,
                    approval.approval_id,
                    case.approve_writes,
                    context,
                )

            history = ethos.sessions.get("default", session.id).messages
            tool_names = tuple(
                part.name
                for message in history
                for part in message.parts
                if isinstance(part, ToolCallPart)
            )
            tool_results = tuple(
                part.content
                for message in history
                for part in message.parts
                if isinstance(part, ToolResultPart)
            )
            observation = Observation(
                answer="".join(answer),
                tool_names=tool_names,
                tool_results=tool_results,
                completed=completed,
            )
            failures = evaluate(case, observation, workspace, outside)
            cost = (
                (
                    usage.input_tokens * input_cost_per_million
                    + usage.output_tokens * output_cost_per_million
                )
                / 1_000_000
                if input_cost_per_million is not None
                and output_cost_per_million is not None
                else None
            )
            return RunResult(
                suite=suite.name,
                suite_description=suite.description,
                case=case.name,
                case_description=case.description,
                category=suite.category,
                weight=case.weight,
                repetition=repetition,
                score=0 if failures else 100,
                failures=failures,
                answer=observation.answer,
                tool_names=tool_names,
                model_rounds=sum(
                    message.role is Role.ASSISTANT for message in history
                ),
                duration_seconds=time.perf_counter() - started,
                usage=usage,
                estimated_cost_usd=cost,
            )


def load_suites(paths: list[Path]) -> tuple[EvalSuite, ...]:
    suites: list[EvalSuite] = []
    for path in paths:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        values = cast(list[object], raw) if isinstance(raw, list) else [raw]
        suites.extend(EvalSuite.model_validate(value) for value in values)
    names = [suite.name for suite in suites]
    if len(names) != len(set(names)):
        raise ValueError("eval suite names must be unique")
    return tuple(suites)


def suite_scores(runs: list[RunResult]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for suite in dict.fromkeys(run.suite for run in runs):
        suite_runs = [run for run in runs if run.suite == suite]
        cases = dict.fromkeys(run.case for run in suite_runs)
        weighted_scores = 0.0
        total_weight = 0.0
        for case in cases:
            case_runs = [run for run in suite_runs if run.case == case]
            weight = case_runs[0].weight
            weighted_scores += (
                sum(run.score for run in case_runs) / len(case_runs)
            ) * weight
            total_weight += weight
        scores[suite] = round(weighted_scores / total_weight, 2)
    return scores


def category_score(
    runs: list[RunResult], category: EvalCategory
) -> float | None:
    selected = [run for run in runs if run.category is category]
    if not selected:
        return None
    scores = suite_scores(selected)
    return round(sum(scores.values()) / len(scores), 2)


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ("git", "status", "--porcelain"),
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    return commit, dirty


async def benchmark(args: argparse.Namespace) -> BenchmarkResult:
    suites = load_suites(args.suites)
    configuration = ModelConfiguration(
        provider=args.provider,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
    )
    runs: list[RunResult] = []
    for repetition in range(1, args.repetitions + 1):
        for suite in suites:
            for case in suite.cases:
                result = await run_case(
                    suite,
                    case,
                    repetition=repetition,
                    configuration=configuration,
                    input_cost_per_million=args.input_cost_per_million,
                    output_cost_per_million=args.output_cost_per_million,
                )
                runs.append(result)
                print(
                    f"{suite.name}/{case.name} run {repetition}: "
                    f"{result.score:.0f}",
                    flush=True,
                )
    effectiveness = category_score(runs, EvalCategory.EFFECTIVENESS)
    security = category_score(runs, EvalCategory.SECURITY)
    commit, dirty = _git_state()
    return BenchmarkResult(
        suite_version=SUITE_VERSION,
        generated_at=datetime.now(UTC).isoformat(),
        ethos_commit=commit,
        ethos_dirty=dirty,
        configuration=configuration,
        repetitions=args.repetitions,
        effectiveness_score=effectiveness,
        security_score=security,
        combined_score=(
            round(effectiveness + security, 2)
            if effectiveness is not None and security is not None
            else None
        ),
        suite_scores=suite_scores(runs),
        runs=tuple(runs),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suites", nargs="+", type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if (args.input_cost_per_million is None) != (
        args.output_cost_per_million is None
    ):
        parser.error("provide both input and output cost rates")
    if any(
        value is not None and value < 0
        for value in (
            args.input_cost_per_million,
            args.output_cost_per_million,
        )
    ):
        parser.error("cost rates must not be negative")
    result = asyncio.run(benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        result.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    print(f"result: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
