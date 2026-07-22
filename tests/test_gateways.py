import asyncio
from collections.abc import AsyncIterator

import pytest

from ethos.commands import CommandRequest, CommandResponse
from ethos.gateways import (
    CommandExecutor,
    Gateway,
    run_gateways,
)


def request(source: str) -> CommandRequest:
    return CommandRequest(
        name="test.echo",
        arguments={"message": source},
        source=source,
        owner_id="owner",
    )


def test_gateway_translates_input_and_renders_responses() -> None:
    rendered: list[str] = []

    class TestGateway(Gateway):
        @property
        def name(self) -> str:
            return "test"

        async def run(self, execute: CommandExecutor) -> None:
            async for response in execute(request(self.name)):
                rendered.append(response.text)

    async def execute(
        command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse(text=str(command.arguments["message"]))

    asyncio.run(run_gateways([TestGateway()], execute))

    assert rendered == ["test"]


def test_gateway_runner_runs_gateways_concurrently() -> None:
    started: set[str] = set()
    both_started = asyncio.Event()

    class TestGateway(Gateway):
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        async def run(self, execute: CommandExecutor) -> None:
            started.add(self.name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=1)

    async def execute(
        _command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse()

    asyncio.run(
        run_gateways([TestGateway("first"), TestGateway("second")], execute)
    )

    assert started == {"first", "second"}


def test_gateway_runner_cancels_and_cleans_up_siblings() -> None:
    sibling_started = asyncio.Event()
    sibling_cleaned_up = asyncio.Event()

    class WaitingGateway(Gateway):
        @property
        def name(self) -> str:
            return "waiting"

        async def run(self, execute: CommandExecutor) -> None:
            sibling_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cleaned_up.set()

    class FailingGateway(Gateway):
        @property
        def name(self) -> str:
            return "failing"

        async def run(self, execute: CommandExecutor) -> None:
            await sibling_started.wait()
            raise RuntimeError("gateway failed")

    async def execute(
        _command: CommandRequest,
    ) -> AsyncIterator[CommandResponse]:
        yield CommandResponse()

    with pytest.raises(ExceptionGroup) as raised:
        asyncio.run(run_gateways([WaitingGateway(), FailingGateway()], execute))

    assert any(
        isinstance(error, RuntimeError) and str(error) == "gateway failed"
        for error in raised.value.exceptions
    )
    assert sibling_cleaned_up.is_set()
