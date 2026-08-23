import re
from pathlib import Path

from ethos.capabilities import RunContext
from ethos.context import SYSTEM_INSTRUCTION, ContextBuilder
from ethos.models import (
    Message,
    Role,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)


def tool_definition(name: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Run {name}",
        parameters={"type": "object", "properties": {}},
    )


def test_context_builder_always_includes_system_instruction() -> None:
    request = ContextBuilder().build(())

    assert len(request.messages) == 2
    assert request.messages[0] == Message(
        role=Role.SYSTEM,
        parts=(TextPart(text=SYSTEM_INSTRUCTION),),
    )
    information = request.messages[1]
    assert information.role is Role.SYSTEM
    information_part = information.parts[0]
    assert isinstance(information_part, TextPart)
    assert re.fullmatch(
        r"Current date: .+\n"
        r"Current time: \d{2}:\d{2}\n"
        r"Timezone: .+ \(UTC[+-]\d{2}:\d{2}\)",
        information_part.text,
    )
    assert request.tools == ()


def test_context_builder_prepends_instructions_and_preserves_tool_order() -> (
    None
):
    history = (Message(role=Role.USER, parts=(TextPart(text="question"),)),)
    tools = (tool_definition("first"), tool_definition("second"))

    request = ContextBuilder().build(
        history,
        ("first instruction", "second instruction"),
        tools,
    )

    assert request.messages[0] == Message(
        role=Role.SYSTEM,
        parts=(TextPart(text=SYSTEM_INSTRUCTION),),
    )
    assert request.messages[1].role is Role.SYSTEM
    assert request.messages[2:] == (
        Message(
            role=Role.SYSTEM,
            parts=(TextPart(text="first instruction"),),
        ),
        Message(
            role=Role.SYSTEM,
            parts=(TextPart(text="second instruction"),),
        ),
        *history,
    )
    assert request.messages[-1] is history[0]
    assert request.tools == tools
    assert history == (
        Message(role=Role.USER, parts=(TextPart(text="question"),)),
    )


def test_context_builder_includes_run_context_without_persisting_it() -> None:
    history = (Message(role=Role.USER, parts=(TextPart(text="question"),)),)
    context = RunContext("project", Path("/workspaces/project"), "session-1")

    request = ContextBuilder().build(history, run_context=context)

    assert request.messages[2] == Message(
        role=Role.SYSTEM,
        parts=(
            TextPart(
                text=(
                    'Run context: {"session_id": "session-1", '
                    '"workspace_name": "project", '
                    '"workspace_path": "/workspaces/project"}'
                )
            ),
        ),
    )
    assert request.messages[3:] == history
    assert history == (
        Message(role=Role.USER, parts=(TextPart(text="question"),)),
    )


def test_context_builder_preserves_tool_call_result_relationships() -> None:
    call = ToolCallPart(
        call_id="call-1",
        name="lookup",
        arguments_json="{}",
    )
    result = ToolResultPart(
        call_id="call-1",
        name="lookup",
        content="result",
    )
    history = (
        Message(role=Role.ASSISTANT, parts=(call,)),
        Message(role=Role.TOOL, parts=(result,)),
    )

    request = ContextBuilder().build(history, ("instruction",))

    assert request.messages[3:] == history
    assert request.messages[3].parts[0] is call
    assert request.messages[4].parts[0] is result
