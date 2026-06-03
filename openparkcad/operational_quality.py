from __future__ import annotations

from heapq import heappop, heappush
from itertools import combinations
from typing import Any

from shapely.geometry import Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.traffic_graph import build_traffic_graph


def operational_quality_report(layout: LayoutResult) -> dict[str, Any]:
    junctions = _junction_reports(layout)
    entrance_throats = _entrance_throat_reports(layout)
    route_risks = _route_risk_reports(layout)
    junction_conflicts = sum(len(item["conflicting_stalls"]) for item in junctions)
    entrance_conflicts = sum(len(item["conflicting_stalls"]) for item in entrance_throats)
    route_risk_score = float(route_risks["risk_score"])
    risk_score = float(junction_conflicts + entrance_conflicts + route_risk_score)
    mode = _operational_quality_mode(layout.site)
    max_allowed_risk_score = _max_allowed_risk_score(layout.site)
    risk_exceeds_limit = (
        max_allowed_risk_score is not None
        and risk_score > max_allowed_risk_score + 1e-9
    )
    blocking_conflicts = (
        _blocking_conflicts(junctions, entrance_throats, route_risks)
        if mode in {"promotion_gate", "hard_reject"} and risk_exceeds_limit
        else []
    )
    promotion_blockers = (
        ["operational_quality_risk_exceeds_limit"]
        if mode in {"promotion_gate", "hard_reject"} and risk_exceeds_limit
        else []
    )
    valid = not (mode == "hard_reject" and risk_exceeds_limit)
    warnings: list[str] = []
    if junction_conflicts:
        warnings.append(f"{junction_conflicts} stall-junction clearance conflicts are reported as soft risks")
    if entrance_conflicts:
        warnings.append(f"{entrance_conflicts} stall-entrance throat conflicts are reported as soft risks")
    if risk_exceeds_limit:
        warnings.append(
            f"operational risk score {risk_score:g} exceeds configured limit {max_allowed_risk_score:g}"
        )
    if route_risk_score:
        warnings.append(f"{route_risk_score:g} route-level operational risk score is reported")
    return {
        "version": "phase5c-1",
        "status": "active_failed" if not valid else "report_only",
        "mode": mode,
        "valid": valid,
        "risk_score": risk_score,
        "max_allowed_risk_score": max_allowed_risk_score,
        "risk_exceeds_limit": risk_exceeds_limit,
        "promotion_blockers": promotion_blockers,
        "blocking_conflicts": blocking_conflicts,
        "junction_count": len(junctions),
        "junction_conflict_count": junction_conflicts,
        "entrance_throat_count": len(entrance_throats),
        "entrance_throat_conflict_count": entrance_conflicts,
        "route_risk_score": route_risk_score,
        "route_risk_count": route_risks["risk_count"],
        "warnings": warnings,
        "junctions": junctions,
        "entrance_throats": entrance_throats,
        "route_risks": route_risks,
    }


def operational_risk_score(layout: LayoutResult) -> float:
    existing = layout.operational_quality.get("risk_score") if layout.operational_quality else None
    if isinstance(existing, int | float):
        return float(existing)
    return float(operational_quality_report(layout)["risk_score"])


def _junction_reports(layout: LayoutResult) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    aisle_polygons = [(aisle, ShapelyPolygon(aisle.polygon)) for aisle in layout.aisles]
    radius = _junction_clearance_radius(layout.site)
    for index, ((left, left_polygon), (right, right_polygon)) in enumerate(combinations(aisle_polygons, 2), start=1):
        point = _junction_point(left_polygon, right_polygon)
        if point is None:
            continue
        zone = ShapelyPoint(point).buffer(radius)
        conflicting = _conflicting_stalls(layout.stalls, zone, point)
        reports.append(
            {
                "id": f"OQ-JUNCTION-{index:03d}",
                "aisle_ids": [left.id, right.id],
                "aisle_roles": [left.role, right.role],
                "point": [float(point[0]), float(point[1])],
                "clearance_radius": radius,
                "conflicting_stall_count": len(conflicting),
                "risk_score": float(len(conflicting)),
                "conflicting_stalls": conflicting,
            }
        )
    return reports


def _entrance_throat_reports(layout: LayoutResult) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for entrance in layout.site.entrances:
        radius = _entrance_clearance_radius(layout.site, entrance.width)
        point = entrance.center
        zone = ShapelyPoint(point).buffer(radius)
        conflicting = _conflicting_stalls(layout.stalls, zone, point)
        reports.append(
            {
                "id": f"OQ-ENTRANCE-{entrance.id}",
                "entrance_id": entrance.id,
                "point": [float(point[0]), float(point[1])],
                "clearance_radius": radius,
                "conflicting_stall_count": len(conflicting),
                "risk_score": float(len(conflicting)),
                "conflicting_stalls": conflicting,
            }
        )
    return reports


def _junction_point(left: ShapelyPolygon, right: ShapelyPolygon) -> tuple[float, float] | None:
    if left.distance(right) > 1e-6:
        return None
    intersection = left.intersection(right)
    if intersection.is_empty:
        return None
    centroid = intersection.centroid
    return (float(centroid.x), float(centroid.y))


def _conflicting_stalls(
    stalls: list[ParkingStall],
    zone,
    point: tuple[float, float],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    junction_point = ShapelyPoint(point)
    for stall in stalls:
        polygon = ShapelyPolygon(stall.polygon)
        overlap_area = polygon.intersection(zone).area
        if overlap_area <= 1e-6:
            continue
        conflicts.append(
            {
                "stall_id": stall.id,
                "served_by_aisle_id": stall.served_by_aisle_id,
                "aisle_side": stall.aisle_side,
                "overlap_area": float(overlap_area),
                "distance_to_center": float(polygon.distance(junction_point)),
            }
        )
    return conflicts


def _junction_clearance_radius(site: SiteSpec) -> float:
    raw = site.optimization.get("operational_junction_clearance_radius", site.aisle_width / 2)
    return _nonnegative_float(raw, site.aisle_width / 2)


def _entrance_clearance_radius(site: SiteSpec, entrance_width: float) -> float:
    default = max(site.aisle_width / 2, entrance_width / 2)
    raw = site.optimization.get("operational_entrance_clearance_radius", default)
    return _nonnegative_float(raw, default)


def _route_risk_reports(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    node_points = {node.id: node.point for node in graph.nodes if node.point is not None}
    entry_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_entry(entrance)}
    exit_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_exit(entrance)}
    max_route_length = _optional_nonnegative_float(layout.site.optimization.get("operational_max_route_length"))
    turnaround_dependency_risk = _nonnegative_float(
        layout.site.optimization.get("operational_turnaround_dependency_risk", 0.0),
        0.0,
    )
    missing_route_risk = _nonnegative_float(
        layout.site.optimization.get("operational_missing_route_risk", 1.0),
        1.0,
    )
    if not entry_nodes or not exit_nodes:
        return {
            "version": "phase5c-1",
            "status": "not_checked_no_entrance_or_exit",
            "route_length_model": "aisle_node_centroid_graph",
            "checked_stall_count": 0,
            "risk_count": 0,
            "risk_score": 0.0,
            "max_route_length": max_route_length,
            "turnaround_dependency_risk": turnaround_dependency_risk,
            "routes": [],
        }

    forward = _weighted_adjacency(graph.edges, node_points, reverse=False)
    reverse = _weighted_adjacency(graph.edges, node_points, reverse=True)
    entry_distances = _shortest_distances(forward, entry_nodes)
    exit_distances = _shortest_distances(reverse, exit_nodes)
    turnaround_parent_ids = {
        aisle.parent_aisle_id
        for aisle in layout.aisles
        if aisle.role == "turnaround" and aisle.parent_aisle_id
    }

    routes = []
    risk_count = 0
    risk_score = 0.0
    for access in graph.stall_access:
        issues: list[str] = []
        aisle_node_id = access.aisle_node_id
        entry_length = _finite_distance(entry_distances.get(aisle_node_id)) if aisle_node_id else None
        exit_length = _finite_distance(exit_distances.get(aisle_node_id)) if aisle_node_id else None
        route_length = entry_length + exit_length if entry_length is not None and exit_length is not None else None
        depends_on_turnaround = bool(access.aisle_id and access.aisle_id in turnaround_parent_ids)
        stall_risk = 0.0
        if entry_length is None:
            issues.append("missing_entry_path")
            stall_risk += missing_route_risk
        if exit_length is None:
            issues.append("missing_exit_path")
            stall_risk += missing_route_risk
        if max_route_length is not None and route_length is not None and route_length > max_route_length + 1e-9:
            issues.append("route_length_exceeds_limit")
            stall_risk += 1.0
        if depends_on_turnaround and turnaround_dependency_risk > 0:
            issues.append("depends_on_dead_end_turnaround")
            stall_risk += turnaround_dependency_risk
        if stall_risk:
            risk_count += 1
            risk_score += stall_risk
        routes.append(
            {
                "stall_id": access.stall_id,
                "aisle_id": access.aisle_id,
                "aisle_node_id": aisle_node_id,
                "entry_path_length": entry_length,
                "exit_path_length": exit_length,
                "route_length": route_length,
                "depends_on_dead_end_turnaround": depends_on_turnaround,
                "issues": issues,
                "risk_score": float(stall_risk),
            }
        )

    return {
        "version": "phase5c-1",
        "status": "active",
        "route_length_model": "aisle_node_centroid_graph",
        "checked_stall_count": len(routes),
        "risk_count": risk_count,
        "risk_score": float(risk_score),
        "max_route_length": max_route_length,
        "turnaround_dependency_risk": turnaround_dependency_risk,
        "routes": routes,
    }


def _weighted_adjacency(edges, node_points: dict[str, tuple[float, float]], reverse: bool) -> dict[str, list[tuple[str, float]]]:
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for edge in edges:
        _add_weighted_arc(adjacency, edge.from_node_id, edge.to_node_id, _edge_weight(edge.from_node_id, edge.to_node_id, node_points))
        if edge.directionality == "two_way":
            _add_weighted_arc(adjacency, edge.to_node_id, edge.from_node_id, _edge_weight(edge.to_node_id, edge.from_node_id, node_points))
    if not reverse:
        return adjacency
    reversed_adjacency: dict[str, list[tuple[str, float]]] = {}
    for source, targets in adjacency.items():
        for target, weight in targets:
            _add_weighted_arc(reversed_adjacency, target, source, weight)
    return reversed_adjacency


def _shortest_distances(adjacency: dict[str, list[tuple[str, float]]], starts: set[str]) -> dict[str, float]:
    distances: dict[str, float] = {}
    heap: list[tuple[float, str]] = []
    for start in sorted(starts):
        heappush(heap, (0.0, start))
    while heap:
        distance, node_id = heappop(heap)
        if node_id in distances:
            continue
        distances[node_id] = distance
        for target, weight in adjacency.get(node_id, []):
            if target not in distances:
                heappush(heap, (distance + weight, target))
    return distances


def _edge_weight(source: str, target: str, node_points: dict[str, tuple[float, float]]) -> float:
    left = node_points.get(source)
    right = node_points.get(target)
    if left is None or right is None:
        return 1.0
    dx = left[0] - right[0]
    dy = left[1] - right[1]
    return float((dx * dx + dy * dy) ** 0.5)


def _add_weighted_arc(adjacency: dict[str, list[tuple[str, float]]], source: str, target: str, weight: float) -> None:
    adjacency.setdefault(source, []).append((target, weight))


def _finite_distance(value: float | None) -> float | None:
    return None if value is None else float(value)


def _operational_quality_mode(site: SiteSpec) -> str:
    raw = site.optimization.get("operational_quality_mode", "score_only")
    mode = str(raw)
    if mode in {"score_only", "promotion_gate", "hard_reject"}:
        return mode
    return "score_only"


def _max_allowed_risk_score(site: SiteSpec) -> float | None:
    raw = site.optimization.get("operational_max_risk_score")
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return None


def _optional_nonnegative_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return None


def _blocking_conflicts(
    junctions: list[dict[str, Any]],
    entrance_throats: list[dict[str, Any]],
    route_risks: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for junction in junctions:
        for stall in _stall_conflicts(junction):
            conflicts.append(
                {
                    "source_type": "junction",
                    "source_id": junction["id"],
                    "aisle_ids": list(junction["aisle_ids"]),
                    "stall_id": stall["stall_id"],
                    "served_by_aisle_id": stall["served_by_aisle_id"],
                    "aisle_side": stall["aisle_side"],
                    "overlap_area": stall["overlap_area"],
                    "distance_to_center": stall["distance_to_center"],
                }
            )
    for throat in entrance_throats:
        for stall in _stall_conflicts(throat):
            conflicts.append(
                {
                    "source_type": "entrance_throat",
                    "source_id": throat["id"],
                    "entrance_id": throat["entrance_id"],
                    "stall_id": stall["stall_id"],
                    "served_by_aisle_id": stall["served_by_aisle_id"],
                    "aisle_side": stall["aisle_side"],
                    "overlap_area": stall["overlap_area"],
                    "distance_to_center": stall["distance_to_center"],
                }
            )
    for route in _route_conflicts(route_risks):
        conflicts.append(route)
    return conflicts


def _stall_conflicts(report_item: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report_item.get("conflicting_stalls", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _route_conflicts(route_risks: dict[str, Any]) -> list[dict[str, Any]]:
    raw = route_risks.get("routes", [])
    if not isinstance(raw, list):
        return []
    conflicts = []
    for route in raw:
        if not isinstance(route, dict) or not route.get("issues"):
            continue
        conflicts.append(
            {
                "source_type": "stall_route",
                "source_id": f"OQ-ROUTE-{route['stall_id']}",
                "stall_id": route["stall_id"],
                "aisle_id": route["aisle_id"],
                "issues": list(route["issues"]),
                "entry_path_length": route["entry_path_length"],
                "exit_path_length": route["exit_path_length"],
                "route_length": route["route_length"],
                "risk_score": route["risk_score"],
            }
        )
    return conflicts


def _entrance_node_id(entrance_id: str) -> str:
    return f"N-ENTRANCE-{entrance_id}"


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


def _nonnegative_float(raw: object, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)
