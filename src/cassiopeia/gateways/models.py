"""Gateway identity and binding models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cassiopeia.shared import EntityId, ExternalId, NonEmptyString, Slug, TimestampedRecord


class GatewayKind(StrEnum):
    """Supported gateway families for 1.0."""

    TUI = "tui"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    OTHER = "other"


class GatewayBindingScope(StrEnum):
    """External location granularity for a gateway binding."""

    USER = "user"
    CHAT = "chat"
    CHANNEL = "channel"
    THREAD = "thread"
    GUILD = "guild"


class GatewayIdentity(TimestampedRecord):
    """Gateway-neutral identity for a configured access point."""

    id: EntityId
    slug: Slug
    name: NonEmptyString
    kind: GatewayKind
    enabled: bool = True


class GatewayBinding(TimestampedRecord):
    """Mapping from an external gateway location to cassiopeia defaults."""

    id: EntityId
    gateway_id: EntityId
    scope: GatewayBindingScope
    external_id: ExternalId
    workspace_id: EntityId
    persona_id: EntityId | None = None
    guild_id: ExternalId | None = None


class GatewayOrigin(BaseModel):
    """Origin metadata attached to a session or message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway_id: EntityId
    binding_id: EntityId | None = None
    external_user_id: ExternalId
    external_conversation_id: ExternalId
    thread_id: ExternalId | None = None
