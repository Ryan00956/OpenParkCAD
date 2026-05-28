from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from openparkcad.models import LayoutResult, ParkingAisle, Point, SiteSpec


@dataclass(frozen=True)
class TrafficNode:
    id: str
    kind: str
    point: Point | None = None
    ref_id: str | None = None


@dataclass(frozen=True)
class TrafficEdge:
    id: str
    from_node_id: str
    to_node_id: str
    directionality: str
    role: str
    aisle_id: str | None = None
    entrance_id: str | None = None


@dataclass(frozen=True)
class StallAccess:
    stall_id: str
    aisle_id: str | None
    aisle_node_id: str | None
    access_side: str | None


@dataclass(frozen=True)
class TrafficGraph:
    nodes: list[TrafficNode] = field(default_factory=list)
    edges: list[TrafficEdge] = field(default_factory=list)
    stall_access: list[StallAccess] = field(default_factory=list)


def build_traffic_graph(layout: LayoutResult) -> TrafficGraph:
    """Build the first Phase 2 graph from Phase 1 layout metadata."""
    nodes: list[TrafficNode] = []
    edges: list[TrafficEdge] = []

    for entrance in layout.site.entrances:
        nodes.append(
            TrafficNode(
                id=_entrance_node_id(entrance.id),
                kind="entrance",
                point=entrance.center,
                ref_id=entrance.id,
            )
        )

    for aisle in layout.aisles:
        nodes.append(
            TrafficNode(
                id=_aisle_node_id(aisle.id),
                kind=_node_kind_for_aisle(aisle),
                point=_centroid(aisle.polygon),
                ref_id=aisle.id,
            )
        )

    for aisle in layout.aisles:
        aisle_node_id = _aisle_node_id(aisle.id)
        if aisle.connected_to_entrance_id:
            edges.append(_entrance_edge(layout.site, aisle, aisle_node_id))
        if aisle.parent_aisle_id:
            edges.append(
                TrafficEdge(
                    id=f"E-PARENT-{aisle.parent_aisle_id}-{aisle.id}",
                    from_node_id=_aisle_node_id(aisle.parent_aisle_id),
                    to_node_id=aisle_node_id,
                    directionality=_layout_aisle_directionality(layout.site),
                    role="aisle_connection",
                    aisle_id=aisle.id,
                )
            )

    aisle_ids = {aisle.id for aisle in layout.aisles}
    stall_access = [
        StallAccess(
            stall_id=stall.id,
            aisle_id=stall.served_by_aisle_id,
            aisle_node_id=_aisle_node_id(stall.served_by_aisle_id) if stall.served_by_aisle_id in aisle_ids else None,
            access_side=stall.aisle_side,
        )
        for stall in layout.stalls
    ]

    return TrafficGraph(nodes=nodes, edges=edges, stall_access=stall_access)


def validate_traffic_graph(graph: TrafficGraph, layout: LayoutResult) -> dict[str, Any]:
    node_ids = {node.id for node in graph.nodes}
    aisle_ids = {aisle.id for aisle in layout.aisles}
    entrance_ids = {entrance.id for entrance in layout.site.entrances}
    entry_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_entry(entrance)}
    exit_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_exit(entrance)}

    missing_edge_nodes = [
        {"edge_id": edge.id, "missing_node_id": node_id}
        for edge in graph.edges
        for node_id in (edge.from_node_id, edge.to_node_id)
        if node_id not in node_ids
    ]
    missing_entrances = [
        edge.entrance_id
        for edge in graph.edges
        if edge.entrance_id and edge.entrance_id not in entrance_ids
    ]
    missing_aisles = [
        access.stall_id
        for access in graph.stall_access
        if not access.aisle_id or access.aisle_id not in aisle_ids
    ]

    reachable_from_entries = _reachable_nodes(graph, entry_nodes, reverse=False)
    can_reach_exits = _reachable_nodes(graph, exit_nodes, reverse=True)

    reachable_aisles = sorted(
        aisle.id
        for aisle in layout.aisles
        if _aisle_node_id(aisle.id) in reachable_from_entries
    )
    unreachable_aisles = sorted(aisle_ids - set(reachable_aisles))
    unreachable_stalls = sorted(
        access.stall_id
        for access in graph.stall_access
        if access.aisle_node_id not in reachable_from_entries
    )
    no_exit_stalls = sorted(
        access.stall_id
        for access in graph.stall_access
        if access.aisle_node_id not in can_reach_exits
    )

    dead_ends = _dead_ends(graph, layout)
    errors = []
    if missing_edge_nodes:
        errors.append("edge_references_missing_node")
    if missing_entrances:
        errors.append("aisle_references_missing_entrance")
    if missing_aisles:
        errors.append("stall_references_missing_aisle")
    if unreachable_aisles:
        errors.append("unreachable_aisles")
    if unreachable_stalls:
        errors.append("unreachable_stalls")
    if layout.stalls and no_exit_stalls:
        errors.append("stalls_without_exit_path")

    return {
        "valid": not errors,
        "errors": errors,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "stall_access_count": len(graph.stall_access),
        "entry_nodes": sorted(entry_nodes),
        "exit_nodes": sorted(exit_nodes),
        "reachable_aisles": reachable_aisles,
        "unreachable_aisles": unreachable_aisles,
        "unreachable_stalls": unreachable_stalls,
        "stalls_without_exit_path": no_exit_stalls,
        "missing_edge_nodes": missing_edge_nodes,
        "missing_entrances": sorted(set(missing_entrances)),
        "stalls_missing_aisles": sorted(missing_aisles),
        "dead_ends": dead_ends,
    }


def traffic_graph_report(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    return {
        "nodes": [asdict(node) for node in graph.nodes],
        "edges": [asdict(edge) for edge in graph.edges],
        "stall_access": [asdict(access) for access in graph.stall_access],
        "validation": validation,
    }


def traffic_graph_summary(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    validation = validate_traffic_graph(graph, layout)
    return {
        "node_count": validation["node_count"],
        "edge_count": validation["edge_count"],
        "stall_access_count": validation["stall_access_count"],
        "valid": validation["valid"],
        "errors": validation["errors"],
        "reachable_aisles": validation["reachable_aisles"],
        "unreachable_aisles": validation["unreachable_aisles"],
        "unreachable_stalls": validation["unreachable_stalls"],
        "stalls_without_exit_path": validation["stalls_without_exit_path"],
        "dead_ends": validation["dead_ends"],
    }


def _entrance_edge(site: SiteSpec, aisle: ParkingAisle, aisle_node_id: str) -> TrafficEdge:
    entrance = next((item for item in site.entrances if item.id == aisle.connected_to_entrance_id), None)
    if entrance and _allows_entry(entrance) and not _allows_exit(entrance):
        from_node_id = _entrance_node_id(entrance.id)
        to_node_id = aisle_node_id
        directionality = "one_way"
    elif entrance and _allows_exit(entrance) and not _allows_entry(entrance):
        from_node_id = aisle_node_id
        to_node_id = _entrance_node_id(entrance.id)
        directionality = "one_way"
    else:
        from_node_id = _entrance_node_id(aisle.connected_to_entrance_id or "")
        to_node_id = aisle_node_id
        directionality = "two_way"
    return TrafficEdge(
        id=f"E-ENTRANCE-{aisle.connected_to_entrance_id}-{aisle.id}",
        from_node_id=from_node_id,
        to_node_id=to_node_id,
        directionality=directionality,
        role="entrance_connection",
        aisle_id=aisle.id,
        entrance_id=aisle.connected_to_entrance_id,
    )


def _reachable_nodes(graph: TrafficGraph, starts: set[str], reverse: bool) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in graph.edges:
        _add_arc(adjacency, edge.from_node_id, edge.to_node_id)
        if edge.directionality == "two_way":
            _add_arc(adjacency, edge.to_node_id, edge.from_node_id)

    if reverse:
        reversed_adjacency: dict[str, set[str]] = {}
        for source, targets in adjacency.items():
            for target in targets:
                _add_arc(reversed_adjacency, target, source)
        adjacency = reversed_adjacency

    seen: set[str] = set()
    stack = list(starts)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(sorted(adjacency.get(node, set()) - seen))
    return seen


def _dead_ends(graph: TrafficGraph, layout: LayoutResult) -> list[dict[str, Any]]:
    undirected_degree: dict[str, int] = {node.id: 0 for node in graph.nodes}
    for edge in graph.edges:
        undirected_degree[edge.from_node_id] = undirected_degree.get(edge.from_node_id, 0) + 1
        undirected_degree[edge.to_node_id] = undirected_degree.get(edge.to_node_id, 0) + 1

    items = []
    for aisle in layout.aisles:
        node_id = _aisle_node_id(aisle.id)
        if aisle.role == "turnaround":
            items.append(
                {
                    "aisle_id": aisle.id,
                    "node_id": node_id,
                    "parent_aisle_id": aisle.parent_aisle_id,
                    "turnaround_present": True,
                    "status": "allowed_with_turnaround",
                }
            )
        elif undirected_degree.get(node_id, 0) <= 1:
            items.append(
                {
                    "aisle_id": aisle.id,
                    "node_id": node_id,
                    "parent_aisle_id": aisle.parent_aisle_id,
                    "turnaround_present": False,
                    "status": "dead_end_without_turnaround",
                }
            )
    return items


def _layout_aisle_directionality(site: SiteSpec) -> str:
    if site.fixed_aisle_class:
        for aisle_class in site.aisle_classes:
            if aisle_class.id == site.fixed_aisle_class:
                return aisle_class.directionality
    for aisle_class in site.aisle_classes:
        if aisle_class.enabled:
            return aisle_class.directionality
    return "two_way"


def _node_kind_for_aisle(aisle: ParkingAisle) -> str:
    if aisle.role == "turnaround":
        return "turnaround"
    if aisle.role == "branch":
        return "branch_aisle"
    if aisle.role == "main":
        return "main_aisle"
    return "aisle"


def _centroid(poly) -> Point:
    return (
        sum(point[0] for point in poly) / len(poly),
        sum(point[1] for point in poly) / len(poly),
    )


def _add_arc(adjacency: dict[str, set[str]], source: str, target: str) -> None:
    adjacency.setdefault(source, set()).add(target)


def _entrance_node_id(entrance_id: str) -> str:
    return f"N-ENTRANCE-{entrance_id}"


def _aisle_node_id(aisle_id: str) -> str:
    return f"N-AISLE-{aisle_id}"


def _allows_entry(entrance) -> bool:
    if entrance.mode == "shared":
        return True
    if entrance.mode == "entry_only":
        return True
    if entrance.mode == "exit_only":
        return False
    return "enter" in entrance.allowed_movements


def _allows_exit(entrance) -> bool:
    if entrance.mode == "shared":
        return True
    if entrance.mode == "entry_only":
        return False
    if entrance.mode == "exit_only":
        return True
    return "exit" in entrance.allowed_movements
