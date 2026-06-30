import pytest
from pydantic import ValidationError

from cassiopeia.personas import (
    ModelConfiguration,
    PersonaDefinition,
    PersonaGatewayAvailability,
    PersonaMemoryPolicy,
    PersonaSessionPolicy,
    PersonaSkillPolicy,
    PersonaToolPolicy,
    PersonaWorkflowPolicy,
)
from cassiopeia.shared import DefinitionScope, MemoryScope


def make_persona() -> PersonaDefinition:
    return PersonaDefinition(
        id="persona-architect",
        slug="architect",
        name="Architect",
        description="Plans technical work",
        role="workspace manager",
        tone="direct",
        behavioural_rules=("Use Canadian spelling",),
        scope=DefinitionScope.WORKSPACE,
        workspace_id="workspace-cassiopeia",
        model=ModelConfiguration(provider="openai", model="gpt-5"),
        skills=PersonaSkillPolicy(selected=("skill-review",)),
        tools=PersonaToolPolicy(allowed=("tool-web-search",)),
        workflows=PersonaWorkflowPolicy(allowed=("workflow-triage",)),
        memory=PersonaMemoryPolicy(
            read_scopes=(MemoryScope.SESSION, MemoryScope.WORKSPACE),
            write_scopes=(MemoryScope.WORKSPACE,),
            auto_create=True,
        ),
        gateway_availability=PersonaGatewayAvailability(gateway_ids=("gateway-tui",)),
        default_session=PersonaSessionPolicy(max_history_messages=50),
    )


def test_persona_definition_round_trips_as_user_authored_json() -> None:
    persona = make_persona()

    assert PersonaDefinition.model_validate_json(persona.model_dump_json()) == persona


def test_persona_definition_rejects_unknown_fields_and_invalid_memory_scope() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PersonaDefinition.model_validate(
            {
                **make_persona().model_dump(mode="json"),
                "extra": "nope",
            }
        )

    with pytest.raises(ValidationError):
        PersonaMemoryPolicy.model_validate({"read_scopes": ["project"]})
