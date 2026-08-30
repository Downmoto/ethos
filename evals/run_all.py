"""Benchmark every configured model and rebuild the public leaderboard."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EVAL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EVAL_ROOT.parent
MAX_RESULT_RUNS = 5
RUN_DIRECTORY_PATTERN = re.compile(r"\d{8}T\d{12}Z")


class ModelEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    input_cost_per_million: float | None = Field(default=None, ge=0)
    output_cost_per_million: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def cost_rates_must_be_paired(self) -> Self:
        if (self.input_cost_per_million is None) != (
            self.output_cost_per_million is None
        ):
            raise ValueError("provide both input and output cost rates")
        return self


class ProviderEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    models: tuple[ModelEntry, ...] = Field(min_length=1)


class AutomationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    repetitions: int = Field(default=3, ge=1)
    providers: tuple[ProviderEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def configurations_must_be_unique(self) -> Self:
        configured = [
            (provider.name, model.name, model.reasoning_effort)
            for provider in self.providers
            for model in provider.models
        ]
        if len(configured) != len(set(configured)):
            raise ValueError("provider/model configurations must be unique")
        return self


def load_config(path: Path) -> AutomationConfig:
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    return AutomationConfig.model_validate(raw)


def _slug(*values: str) -> str:
    return "-".join(
        re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") for value in values
    )


def cull_old_results(root: Path) -> None:
    if not root.is_dir():
        return
    runs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and RUN_DIRECTORY_PATTERN.fullmatch(path.name)
    )
    for path in runs[:-MAX_RESULT_RUNS]:
        shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=EVAL_ROOT / "models.json"
    )
    parser.add_argument("--cases", type=Path, default=EVAL_ROOT / "suites")
    parser.add_argument("--results", type=Path, default=EVAL_ROOT / "results")
    args = parser.parse_args()
    config = load_config(args.config)
    cases = sorted(args.cases.glob("*.json"))
    if not cases:
        parser.error(f"no JSON cases found in {args.cases}")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    result_directory = args.results / timestamp
    leaderboard = result_directory / "LEADERBOARD.md"
    results: list[Path] = []
    failures: list[str] = []
    for provider in config.providers:
        for model in provider.models:
            label = f"{provider.name}/{model.name} ({model.reasoning_effort})"
            output = result_directory / (
                _slug(provider.name, model.name, model.reasoning_effort)
                + ".json"
            )
            command = [
                sys.executable,
                "-m",
                "evals.engine",
                "--provider",
                provider.name,
                "--model",
                model.name,
                "--reasoning-effort",
                model.reasoning_effort,
                "--repetitions",
                str(config.repetitions),
                "--output",
                str(output),
            ]
            if model.input_cost_per_million is not None:
                command.extend(
                    (
                        "--input-cost-per-million",
                        str(model.input_cost_per_million),
                        "--output-cost-per-million",
                        str(model.output_cost_per_million),
                    )
                )
            command.extend(str(case) for case in cases)
            print(f"\nBenchmarking {label}", flush=True)
            try:
                subprocess.run(command, cwd=PROJECT_ROOT, check=True)
            except subprocess.CalledProcessError:
                failures.append(label)
            else:
                results.append(output)

    if results:
        subprocess.run(
            (
                sys.executable,
                "-m",
                "evals.leaderboard",
                "--output",
                str(leaderboard),
                *(str(result) for result in results),
            ),
            cwd=PROJECT_ROOT,
            check=True,
        )
        print(f"\nLeaderboard: {leaderboard}")
    if failures:
        print("\nFailed configurations:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
    cull_old_results(args.results)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
