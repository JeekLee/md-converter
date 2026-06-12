from md_converter._diagram import ShapeNode, ShapeEdge, DiagramGraph, graph_to_mermaid


def test_simple_chain():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="ellipse", label="시작"),
            ShapeNode(id="2", shape_type="rect",    label="처리"),
            ShapeNode(id="3", shape_type="ellipse", label="종료"),
        ],
        edges=[
            ShapeEdge(from_id="1", to_id="2", label="", arrow=True),
            ShapeEdge(from_id="2", to_id="3", label="", arrow=True),
        ],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert result.startswith("graph TD")
    assert "n1([시작]) --> n2[처리]" in result
    assert "n2 --> n3([종료])" in result


def test_diamond_with_edge_labels():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="diamond", label="오류?"),
            ShapeNode(id="2", shape_type="rect",    label="처리"),
            ShapeNode(id="3", shape_type="rect",    label="에러"),
        ],
        edges=[
            ShapeEdge(from_id="1", to_id="2", label="아니오", arrow=True),
            ShapeEdge(from_id="1", to_id="3", label="예",    arrow=True),
        ],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "n1{오류?}" in result
    assert "|아니오|" in result
    assert "|예|" in result


def test_no_arrow_uses_line():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="rect", label="A"),
            ShapeNode(id="2", shape_type="rect", label="B"),
        ],
        edges=[ShapeEdge(from_id="1", to_id="2", label="", arrow=False)],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "---" in result
    assert "-->" not in result


def test_empty_graph_returns_none():
    assert graph_to_mermaid(DiagramGraph(nodes=[], edges=[])) is None


def test_isolated_node_appears():
    graph = DiagramGraph(
        nodes=[
            ShapeNode(id="1", shape_type="rect",    label="고립"),
            ShapeNode(id="2", shape_type="ellipse", label="연결됨"),
            ShapeNode(id="3", shape_type="rect",    label="종착"),
        ],
        edges=[ShapeEdge(from_id="2", to_id="3", label="", arrow=True)],
    )
    result = graph_to_mermaid(graph)
    assert result is not None
    assert "n1[고립]" in result
    assert "n2([연결됨]) --> n3[종착]" in result
