import asyncio

import pytest

from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope, EventSource
from cassiopeia.events.types import EventType


def test_listener_registry_delivers_events_in_registration_order() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_INITIALISED,
        source=EventSource(name="test"),
    )

    async def first_listener(_event: EventEnvelope) -> None:
        delivered_to.append("first")

    async def second_listener(_event: EventEnvelope) -> None:
        delivered_to.append("second")

    registry.register(first_listener)
    registry.register(second_listener)

    asyncio.run(registry.deliver(event))

    assert delivered_to == ["first", "second"]


def test_listener_registry_delivers_only_matching_event_type() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def started_listener(_event: EventEnvelope) -> None:
        delivered_to.append("started")

    async def initialised_listener(_event: EventEnvelope) -> None:
        delivered_to.append("initialised")

    registry.register(started_listener, event_type=[EventType.APP_STARTED])
    registry.register(
        initialised_listener, event_type=[EventType.APP_INITIALISED]
    )

    asyncio.run(registry.deliver(event))

    assert delivered_to == ["started"]


def test_listener_registry_continues_after_listener_failure() -> None:
    registry = EventListenerRegistry()
    delivered_to: list[str] = []
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def failing_listener(_event: EventEnvelope) -> None:
        delivered_to.append("failing")
        raise RuntimeError("listener failed")

    async def successful_listener(_event: EventEnvelope) -> None:
        delivered_to.append("successful")

    registry.register(failing_listener)
    registry.register(successful_listener)

    with pytest.raises(ExceptionGroup) as error:
        asyncio.run(registry.deliver(event))

    assert delivered_to == ["failing", "successful"]
    assert len(error.value.exceptions) == 1


def test_listener_registry_collects_multiple_failures() -> None:
    registry = EventListenerRegistry()
    event = EventEnvelope(
        type=EventType.APP_STARTED,
        source=EventSource(name="test"),
    )

    async def first_listener(_event: EventEnvelope) -> None:
        raise RuntimeError("first failed")

    async def second_listener(_event: EventEnvelope) -> None:
        raise ValueError("second failed")

    registry.register(first_listener)
    registry.register(second_listener)

    with pytest.raises(ExceptionGroup) as error:
        asyncio.run(registry.deliver(event))

    assert len(error.value.exceptions) == 2
