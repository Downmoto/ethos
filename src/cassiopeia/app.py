import asyncio

from cassiopeia.events import EVENT_EMITTER, event_factory
from cassiopeia.events.models import EventPayload
from cassiopeia.events.types import EventType


async def async_main() -> None:
    await EVENT_EMITTER.emit(
        event=event_factory(
            event_type=EventType.APP_STARTED,
            location=__name__,
            details="cassiopeia app started",
            payload=EventPayload(),
            tags=("test",),
        )
    )

    await EVENT_EMITTER.emit(
        event=event_factory(
            event_type=EventType.APP_INITIALISED,
            location=__name__,
            details="configs loaded, app initialised",
            payload=EventPayload(),
            tags=("test",),
        )
    )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
