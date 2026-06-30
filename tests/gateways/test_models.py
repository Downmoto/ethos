from pydantic import ValidationError

from cassiopeia.gateways import (
    GatewayBinding,
    GatewayBindingScope,
    GatewayIdentity,
    GatewayKind,
    GatewayOrigin,
)


def test_gateway_identity_and_binding_round_trip_by_id() -> None:
    identity = GatewayIdentity(
        id="gateway-telegram",
        slug="telegram",
        name="Telegram",
        kind=GatewayKind.TELEGRAM,
    )
    binding = GatewayBinding(
        id="binding-personal-chat",
        gateway_id=identity.id,
        scope=GatewayBindingScope.CHAT,
        external_id="123456789",
        workspace_id="workspace-personal",
        persona_id="persona-assistant",
    )

    assert GatewayBinding.model_validate_json(binding.model_dump_json()) == binding


def test_gateway_origin_rejects_unknown_fields() -> None:
    try:
        GatewayOrigin.model_validate(
            {
                "gateway_id": "gateway-telegram",
                "external_user_id": "user-1",
                "external_conversation_id": "chat-1",
                "raw_payload": {},
            }
        )
    except ValidationError as error:
        assert "Extra inputs are not permitted" in str(error)
    else:  # pragma: no cover
        raise AssertionError("unknown gateway origin fields should fail validation")
