from pydantic import BaseModel, Field


class EventsConfig(BaseModel):
    """Events module config object"""

    flush_rate: int = Field(default=5)
