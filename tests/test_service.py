import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from ethos.home import initialise_home
from ethos.runtime import AgentRuntime, PromptStreamEvent
from ethos.service import Ethos, RequestContext


def context() -> RequestContext:
    return RequestContext("test", "owner", {"client": "pytest"})


def test_service_shares_workspace_and_session_behaviour(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            workspace = await ethos.create_workspace("health", context())
            assert workspace.name == "health"
            assert [
                item.name for item in await ethos.list_workspaces(context())
            ] == [
                "default",
                "health",
            ]

            session = await ethos.create_session("health", context())
            assert (
                await ethos.show_session("health", session.id, context())
            ).id == session.id
            archived = await ethos.archive_session(
                "health", session.id, context()
            )
            assert archived.archived

    asyncio.run(exercise())


def test_service_translates_runtime_streams(tmp_path: Path) -> None:
    home = initialise_home(tmp_path / ".ethos")

    async def exercise() -> None:
        with Ethos(home) as ethos:
            session = await ethos.create_session("default", context())

            class FakeRuntime:
                async def run(
                    self, prompt: str, workspace: str, session_id: str
                ) -> AsyncIterator[PromptStreamEvent]:
                    assert (prompt, workspace, session_id) == (
                        "hello",
                        "default",
                        session.id,
                    )
                    yield PromptStreamEvent(text="reply")

            ethos._agent = cast(AgentRuntime, FakeRuntime())
            chunks = [
                chunk
                async for chunk in ethos.chat(
                    "default", session.id, "hello", context()
                )
            ]

            assert [chunk.text for chunk in chunks] == ["reply"]
            assert chunks[0].workspace == "default"
            assert chunks[0].session_id == session.id

    asyncio.run(exercise())
