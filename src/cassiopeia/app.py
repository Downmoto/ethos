import asyncio

from cassiopeia.events.emitters import EnvelopeEventEmitter
from cassiopeia.events.listeners import EventListenerRegistry
from cassiopeia.events.models import EventEnvelope, EventPayload, EventSource, event_factory
from cassiopeia.events.sinks import InMemoryEventSink
from cassiopeia.events.types import EventType


def _register_event_listener_registry() -> EventListenerRegistry:
    return EventListenerRegistry()


def _register_event_emitter(
    sink: InMemoryEventSink, dispatcher: EventListenerRegistry
) -> EnvelopeEventEmitter:
    return EnvelopeEventEmitter(sink, dispatcher)


async def _global_print_event_listener(event: EventEnvelope) -> None:
    print("=== FROM PRINT LISTENER ===")
    print(f"event::<{event.type}>")
    print(f"id::<{event.id}>")
    print(f"source.name::<{event.source.name}>, source.detail::<{event.source.detail}>")
    print()


async def async_main():
    TEMP_INMEM_SINK = InMemoryEventSink()

    event_listener_reg = _register_event_listener_registry()
    event_listener_reg.register(_global_print_event_listener)

    event_emitter = _register_event_emitter(sink=TEMP_INMEM_SINK, dispatcher=event_listener_reg)

    await event_emitter.emit(
        event=event_factory(
            event_type=EventType.APP_STARTED,
            source=EventSource(name="app:async_main", detail="main async entry point"),
            payload=EventPayload(),
            tags=("test",),
        )
    )

    await event_emitter.emit(
        event=event_factory(
            event_type=EventType.APP_INITIALISED,
            source=EventSource(name="app:async_main", detail="cassiopeia initialised"),
            payload=EventPayload(),
            tags=("test",),
        )
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
