from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ShapeNode:
    id: str
    shape_type: str  # "rect" | "ellipse" | "diamond" | "other"
    label: str


@dataclass
class ShapeEdge:
    from_id: str
    to_id: str
    label: str
    arrow: bool  # True = -->, False = ---


@dataclass
class DiagramGraph:
    nodes: list[ShapeNode]
    edges: list[ShapeEdge]


_MERMAID_SHAPE: dict[str, str] = {
    "rect":    "[{label}]",
    "ellipse": "([{label}])",
    "diamond": "{{{label}}}",
    "other":   "[{label}]",
}


def graph_to_mermaid(graph: DiagramGraph) -> str | None:
    if not graph.nodes:
        return None

    node_map = {n.id: n for n in graph.nodes}
    emitted: set[str] = set()
    lines = ["graph TD"]

    def _node_ref(node_id: str) -> str:
        node = node_map.get(node_id)
        if node is None:
            return f"n{node_id}"
        if node_id in emitted:
            return f"n{node_id}"
        emitted.add(node_id)
        label = node.label.replace('"', "'")
        shape = _MERMAID_SHAPE.get(node.shape_type, "[{label}]").format(label=label)
        return f"n{node_id}{shape}"

    for edge in graph.edges:
        from_ref = _node_ref(edge.from_id)
        to_ref   = _node_ref(edge.to_id)
        connector = "-->" if edge.arrow else "---"
        label_part = f"|{edge.label}|" if edge.label else ""
        lines.append(f"  {from_ref} {connector}{label_part} {to_ref}")

    for node in graph.nodes:
        if node.id not in emitted:
            label = node.label.replace('"', "'")
            shape = _MERMAID_SHAPE.get(node.shape_type, "[{label}]").format(label=label)
            lines.append(f"  n{node.id}{shape}")

    return "\n".join(lines)
