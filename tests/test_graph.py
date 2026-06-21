from cassiopeia.config import Settings
from cassiopeia.graph import build_graph


def test_graph_returns_response() -> None:
    graph = build_graph(Settings(model_name="test-model"))

    result = graph.invoke({"messages": ["hello"]})

    assert result["response"] == "cassiopeia received: hello via test-model"
