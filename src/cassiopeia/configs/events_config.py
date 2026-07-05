from pydantic import BaseModel, Field


class EventsConfig(BaseModel):
    """Events module config object"""

    enabled: bool = True
    print_events: bool = False
    flush_rate: int = Field(default=5, ge=1)
