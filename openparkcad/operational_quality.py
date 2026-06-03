from __future__ import annotations

from heapq import heappop, heappush
from itertools import combinations
from typing import Any

from shapely import affinity
from shapely.geometry import LineString, Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.traffic_graph import build_traffic_graph


_PASSING_BAY_FEATURE_TYPES = {
    "passing_bay",
    "passing_bay_area",
}


def operational_quality_report(layout: LayoutResult) -> dict[str, Any]:
    junctions = _junction_reports(layout)
    entrance_throats = _entrance_throat_reports(layout)
    route_risks = _route_risk_reports(layout)
    directionality_risks = _directionality_risk_reports(layout)
    narrow_two_way_risks = _narrow_two_way_risk_reports(layout)
    junction_conflicts = sum(len(item["conflicting_stalls"]) for item in junctions)
    entrance_conflicts = sum(len(item["conflicting_stalls"]) for item in entrance_throats)
    route_risk_score = float(route_risks["risk_score"])
    directionality_risk_score = float(directionality_risks["risk_score"])
    narrow_two_way_risk_score = float(narrow_two_way_risks["risk_score"])
    risk_score = float(
        junction_conflicts
        + entrance_conflicts
        + route_risk_score
        + directionality_risk_score
        + narrow_two_way_risk_score
    )
    mode = _operational_quality_mode(layout.site)
    max_allowed_risk_score = _max_allowed_risk_score(layout.site)
    risk_exceeds_limit = (
        max_allowed_risk_score is not None
        and risk_score > max_allowed_risk_score + 1e-9
    )
    blocking_conflicts = (
        _blocking_conflicts(junctions, entrance_throats, route_risks, directionality_risks, narrow_two_way_risks)
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
    if directionality_risk_score:
        warnings.append(f"{directionality_risk_score:g} directionality operational risk score is reported")
    if narrow_two_way_risk_score:
        warnings.append(f"{narrow_two_way_risk_score:g} narrow two-way operational risk score is reported")
    return {
        "version": "phase5j-1",
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
        "route_summary": route_risks["summary"],
        "route_summary_risks": route_risks["summary_risks"],
        "directionality_risk_score": directionality_risk_score,
        "directionality_risk_count": directionality_risks["risk_count"],
        "directionality_summary": directionality_risks["summary"],
        "directionality_summary_risks": directionality_risks["summary_risks"],
        "narrow_two_way_risk_score": narrow_two_way_risk_score,
        "narrow_two_way_risk_count": narrow_two_way_risks["risk_count"],
        "narrow_two_way_summary": narrow_two_way_risks["summary"],
        "narrow_two_way_summary_risks": narrow_two_way_risks["summary_risks"],
        "warnings": warnings,
        "junctions": junctions,
        "entrance_throats": entrance_throats,
        "route_risks": route_risks,
        "directionality_risks": directionality_risks,
        "narrow_two_way_risks": narrow_two_way_risks,
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
    max_turnaround_dependency_ratio = _optional_ratio(
        layout.site.optimization.get("operational_max_turnaround_dependency_ratio")
    )
    max_average_route_length = _optional_nonnegative_float(
        layout.site.optimization.get("operational_max_average_route_length")
    )
    max_long_route_ratio = _optional_ratio(
        layout.site.optimization.get("operational_max_long_route_ratio")
    )
    turnaround_dependency_risk = _nonnegative_float(
        layout.site.optimization.get("operational_turnaround_dependency_risk", 0.0),
        0.0,
    )
    turnaround_dependency_ratio_risk = _nonnegative_float(
        layout.site.optimization.get("operational_turnaround_dependency_ratio_risk", 1.0),
        1.0,
    )
    average_route_length_risk = _nonnegative_float(
        layout.site.optimization.get("operational_average_route_length_risk", 1.0),
        1.0,
    )
    long_route_ratio_risk = _nonnegative_float(
        layout.site.optimization.get("operational_long_route_ratio_risk", 1.0),
        1.0,
    )
    missing_route_risk = _nonnegative_float(
        layout.site.optimization.get("operational_missing_route_risk", 1.0),
        1.0,
    )
    if not entry_nodes or not exit_nodes:
        return {
            "version": "phase5f-1",
            "status": "not_checked_no_entrance_or_exit",
            "route_length_model": "aisle_node_centroid_graph",
            "checked_stall_count": 0,
            "risk_count": 0,
            "risk_score": 0.0,
            "stall_route_risk_score": 0.0,
            "summary_risk_score": 0.0,
            "max_route_length": max_route_length,
            "max_turnaround_dependency_ratio": max_turnaround_dependency_ratio,
            "max_average_route_length": max_average_route_length,
            "max_long_route_ratio": max_long_route_ratio,
            "turnaround_dependency_risk": turnaround_dependency_risk,
            "turnaround_dependency_ratio_risk": turnaround_dependency_ratio_risk,
            "average_route_length_risk": average_route_length_risk,
            "long_route_ratio_risk": long_route_ratio_risk,
            "summary": _route_summary([]),
            "summary_risks": [],
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

    summary = _route_summary(routes)
    summary_risks = _route_summary_risks(
        summary,
        max_turnaround_dependency_ratio,
        turnaround_dependency_ratio_risk,
        max_average_route_length,
        average_route_length_risk,
        max_long_route_ratio,
        long_route_ratio_risk,
    )
    summary_risk_score = sum(float(item["risk_score"]) for item in summary_risks)
    return {
        "version": "phase5f-1",
        "status": "active",
        "route_length_model": "aisle_node_centroid_graph",
        "checked_stall_count": len(routes),
        "risk_count": risk_count + len(summary_risks),
        "risk_score": float(risk_score + summary_risk_score),
        "stall_route_risk_score": float(risk_score),
        "summary_risk_score": float(summary_risk_score),
        "max_route_length": max_route_length,
        "max_turnaround_dependency_ratio": max_turnaround_dependency_ratio,
        "max_average_route_length": max_average_route_length,
        "max_long_route_ratio": max_long_route_ratio,
        "turnaround_dependency_risk": turnaround_dependency_risk,
        "turnaround_dependency_ratio_risk": turnaround_dependency_ratio_risk,
        "average_route_length_risk": average_route_length_risk,
        "long_route_ratio_risk": long_route_ratio_risk,
        "summary": summary,
        "summary_risks": summary_risks,
        "routes": routes,
    }


def _directionality_risk_reports(layout: LayoutResult) -> dict[str, Any]:
    graph = build_traffic_graph(layout)
    entry_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_entry(entrance)}
    exit_nodes = {_entrance_node_id(entrance.id) for entrance in layout.site.entrances if _allows_exit(entrance)}
    issue_risk = _nonnegative_float(
        layout.site.optimization.get("operational_directionality_issue_risk", 0.0),
        0.0,
    )
    max_issue_ratio = _optional_ratio(layout.site.optimization.get("operational_max_directionality_issue_ratio"))
    issue_ratio_risk = _nonnegative_float(
        layout.site.optimization.get("operational_directionality_issue_ratio_risk", 1.0),
        1.0,
    )
    if not entry_nodes or not exit_nodes:
        summary = _directionality_summary([], [], len(graph.stall_access))
        return {
            "version": "phase5g-1",
            "status": "not_checked_no_entrance_or_exit",
            "risk_count": 0,
            "risk_score": 0.0,
            "stall_issue_risk_score": 0.0,
            "summary_risk_score": 0.0,
            "directionality_issue_risk": issue_risk,
            "max_directionality_issue_ratio": max_issue_ratio,
            "directionality_issue_ratio_risk": issue_ratio_risk,
            "summary": summary,
            "summary_risks": [],
            "node_issues": [],
            "stall_issues": [],
        }

    reachable_from_entries = _reachable_node_ids(graph.edges, entry_nodes, reverse=False)
    can_reach_exits = _reachable_node_ids(graph.edges, exit_nodes, reverse=True)
    node_issues = _directionality_node_issues(graph, reachable_from_entries, can_reach_exits)
    node_issue_by_id = {item["node_id"]: item for item in node_issues}
    stall_issues = _directionality_stall_issues(graph.stall_access, node_issue_by_id, issue_risk)
    stall_issue_risk_score = sum(float(item["risk_score"]) for item in stall_issues)
    summary = _directionality_summary(node_issues, stall_issues, len(graph.stall_access))
    summary_risks = _directionality_summary_risks(summary, max_issue_ratio, issue_ratio_risk)
    summary_risk_score = sum(float(item["risk_score"]) for item in summary_risks)
    return {
        "version": "phase5g-1",
        "status": "active",
        "risk_count": len([item for item in stall_issues if float(item["risk_score"]) > 0]) + len(summary_risks),
        "risk_score": float(stall_issue_risk_score + summary_risk_score),
        "stall_issue_risk_score": float(stall_issue_risk_score),
        "summary_risk_score": float(summary_risk_score),
        "directionality_issue_risk": issue_risk,
        "max_directionality_issue_ratio": max_issue_ratio,
        "directionality_issue_ratio_risk": issue_ratio_risk,
        "summary": summary,
        "summary_risks": summary_risks,
        "node_issues": node_issues,
        "stall_issues": stall_issues,
    }


def _narrow_two_way_risk_reports(layout: LayoutResult) -> dict[str, Any]:
    aisle_class = _selected_aisle_class(layout.site)
    passing_bays = _passing_bay_markers(layout.site)
    min_passing_bays = _optional_nonnegative_int(
        layout.site.optimization.get("operational_min_passing_bays")
    )
    issue_risk = _nonnegative_float(
        layout.site.optimization.get("operational_narrow_two_way_issue_risk", 0.0),
        0.0,
    )
    max_stall_ratio = _optional_ratio(layout.site.optimization.get("operational_max_narrow_two_way_stall_ratio"))
    stall_ratio_risk = _nonnegative_float(
        layout.site.optimization.get("operational_narrow_two_way_stall_ratio_risk", 1.0),
        1.0,
    )
    passing_bay_touch_tolerance = _nonnegative_float(
        layout.site.optimization.get("operational_passing_bay_touch_tolerance", 0.25),
        0.25,
    )
    min_passing_bay_area = _optional_nonnegative_float(
        layout.site.optimization.get("operational_min_passing_bay_area")
    )
    passing_bay_geometry_issue_risk = _nonnegative_float(
        layout.site.optimization.get("operational_passing_bay_geometry_issue_risk", 0.0),
        0.0,
    )
    passing_bay_shortage_risk = _nonnegative_float(
        layout.site.optimization.get("operational_passing_bay_shortage_risk", 1.0),
        1.0,
    )
    if not _is_narrow_two_way_class(aisle_class):
        summary = _narrow_two_way_summary(
            [],
            [],
            len(layout.stalls),
            aisle_class,
            passing_bays,
            [],
            min_passing_bays,
        )
        return {
            "version": "phase5j-1",
            "status": "not_applicable",
            "risk_count": 0,
            "risk_score": 0.0,
            "stall_issue_risk_score": 0.0,
            "passing_bay_issue_risk_score": 0.0,
            "summary_risk_score": 0.0,
            "narrow_two_way_issue_risk": issue_risk,
            "max_narrow_two_way_stall_ratio": max_stall_ratio,
            "narrow_two_way_stall_ratio_risk": stall_ratio_risk,
            "passing_bay_touch_tolerance": passing_bay_touch_tolerance,
            "min_passing_bay_area": min_passing_bay_area,
            "passing_bay_geometry_issue_risk": passing_bay_geometry_issue_risk,
            "min_passing_bays": min_passing_bays,
            "passing_bay_shortage_risk": passing_bay_shortage_risk,
            "selected_aisle_class": _aisle_class_report(aisle_class),
            "passing_bay_model_available": summary["passing_bay_model_available"],
            "passing_bays": passing_bays,
            "summary": summary,
            "summary_risks": [],
            "aisle_issues": [],
            "stall_issues": [],
        }

    narrow_aisles = [aisle for aisle in layout.aisles if aisle.role != "turnaround"]
    passing_bay_reports = _passing_bay_reports(
        passing_bays,
        narrow_aisles,
        passing_bay_touch_tolerance,
        min_passing_bay_area,
        passing_bay_geometry_issue_risk,
    )
    usable_passing_bay_count = sum(1 for item in passing_bay_reports if item["usable"])
    passing_bay_model_available = bool(passing_bay_reports)
    aisle_issue = (
        "narrow_two_way_passing_bay_spacing_not_checked"
        if usable_passing_bay_count
        else "narrow_two_way_without_usable_passing_bay"
        if passing_bay_model_available
        else "narrow_two_way_without_passing_bay_model"
    )
    stall_issue = (
        "stall_served_by_narrow_two_way_aisle_pending_passing_bay_spacing_check"
        if usable_passing_bay_count
        else "stall_served_by_narrow_two_way_aisle_without_usable_passing_bay"
        if passing_bay_model_available
        else "stall_served_by_narrow_two_way_aisle_without_passing_bay_model"
    )
    aisle_issues = [
        {
            "aisle_id": aisle.id,
            "role": aisle.role,
            "issue": aisle_issue,
            "passing_bay_model_available": passing_bay_model_available,
            "passing_bay_marker_count": len(passing_bays),
        }
        for aisle in layout.aisles
        if aisle.role != "turnaround"
    ]
    narrow_aisle_ids = {item["aisle_id"] for item in aisle_issues}
    stall_issues = [
        {
            "stall_id": stall.id,
            "aisle_id": stall.served_by_aisle_id,
            "issue": stall_issue,
            "risk_score": float(issue_risk),
        }
        for stall in layout.stalls
        if stall.served_by_aisle_id in narrow_aisle_ids
    ]
    stall_issue_risk_score = sum(float(item["risk_score"]) for item in stall_issues)
    passing_bay_issue_risk_score = sum(float(item["risk_score"]) for item in passing_bay_reports)
    summary = _narrow_two_way_summary(
        aisle_issues,
        stall_issues,
        len(layout.stalls),
        aisle_class,
        passing_bays,
        passing_bay_reports,
        min_passing_bays,
    )
    summary_risks = _narrow_two_way_summary_risks(
        summary,
        max_stall_ratio,
        stall_ratio_risk,
        passing_bay_shortage_risk,
    )
    summary_risk_score = sum(float(item["risk_score"]) for item in summary_risks)
    return {
        "version": "phase5j-1",
        "status": "active",
        "risk_count": (
            len([item for item in stall_issues if float(item["risk_score"]) > 0])
            + len([item for item in passing_bay_reports if float(item["risk_score"]) > 0])
            + len(summary_risks)
        ),
        "risk_score": float(stall_issue_risk_score + passing_bay_issue_risk_score + summary_risk_score),
        "stall_issue_risk_score": float(stall_issue_risk_score),
        "passing_bay_issue_risk_score": float(passing_bay_issue_risk_score),
        "summary_risk_score": float(summary_risk_score),
        "narrow_two_way_issue_risk": issue_risk,
        "max_narrow_two_way_stall_ratio": max_stall_ratio,
        "narrow_two_way_stall_ratio_risk": stall_ratio_risk,
        "passing_bay_touch_tolerance": passing_bay_touch_tolerance,
        "min_passing_bay_area": min_passing_bay_area,
        "passing_bay_geometry_issue_risk": passing_bay_geometry_issue_risk,
        "min_passing_bays": min_passing_bays,
        "passing_bay_shortage_risk": passing_bay_shortage_risk,
        "selected_aisle_class": _aisle_class_report(aisle_class),
        "passing_bay_model_available": summary["passing_bay_model_available"],
        "passing_bays": passing_bay_reports,
        "summary": summary,
        "summary_risks": summary_risks,
        "aisle_issues": aisle_issues,
        "stall_issues": stall_issues,
    }


def _narrow_two_way_summary(
    aisle_issues: list[dict[str, Any]],
    stall_issues: list[dict[str, Any]],
    checked_stall_count: int,
    aisle_class,
    passing_bays: list[dict[str, Any]],
    passing_bay_reports: list[dict[str, Any]],
    min_passing_bays: int | None,
) -> dict[str, Any]:
    affected_stall_count = len(stall_issues)
    is_narrow_two_way = _is_narrow_two_way_class(aisle_class)
    passing_bay_marker_count = len(passing_bays)
    usable_passing_bay_count = sum(1 for item in passing_bay_reports if item.get("usable"))
    invalid_passing_bay_count = len(passing_bay_reports) - usable_passing_bay_count
    shortage_count = (
        max(min_passing_bays - usable_passing_bay_count, 0)
        if is_narrow_two_way and min_passing_bays is not None
        else 0
    )
    return {
        "selected_aisle_class": _aisle_class_report(aisle_class),
        "is_narrow_two_way": is_narrow_two_way,
        "passing_bay_model_available": bool(passing_bays),
        "passing_bay_marker_count": passing_bay_marker_count,
        "usable_passing_bay_count": usable_passing_bay_count,
        "invalid_passing_bay_count": invalid_passing_bay_count,
        "passing_bay_geometry_checked": is_narrow_two_way,
        "min_passing_bays": min_passing_bays,
        "passing_bay_shortage_count": shortage_count,
        "narrow_two_way_aisle_count": len(aisle_issues),
        "affected_stall_count": affected_stall_count,
        "checked_stall_count": checked_stall_count,
        "affected_stall_ratio": (
            float(affected_stall_count / checked_stall_count)
            if checked_stall_count
            else 0.0
        ),
        "issue": (
            (
                "narrow_two_way_passing_bay_geometry_not_checked"
                if passing_bays
                else "narrow_two_way_without_passing_bay_model"
            )
            if aisle_issues
            else None
        ),
    }


def _narrow_two_way_summary_risks(
    summary: dict[str, Any],
    max_stall_ratio: float | None,
    stall_ratio_risk: float,
    passing_bay_shortage_risk: float,
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    ratio = summary.get("affected_stall_ratio")
    if (
        max_stall_ratio is not None
        and isinstance(ratio, int | float)
        and float(ratio) > max_stall_ratio + 1e-9
    ):
        risks.append(
            {
                "id": "OQ-NARROW-TWO-WAY-STALL-RATIO",
                "issue": "narrow_two_way_stall_ratio_exceeds_limit",
                "affected_stall_ratio": float(ratio),
                "max_narrow_two_way_stall_ratio": max_stall_ratio,
                "affected_stall_count": summary["affected_stall_count"],
                "checked_stall_count": summary["checked_stall_count"],
                "risk_score": float(stall_ratio_risk),
            }
        )
    shortage_count = summary.get("passing_bay_shortage_count")
    if isinstance(shortage_count, int | float) and shortage_count > 0:
        risks.append(
            {
                "id": "OQ-NARROW-TWO-WAY-PASSING-BAY-SHORTAGE",
                "issue": "passing_bay_count_below_minimum",
                "passing_bay_marker_count": summary["passing_bay_marker_count"],
                "usable_passing_bay_count": summary["usable_passing_bay_count"],
                "min_passing_bays": summary["min_passing_bays"],
                "passing_bay_shortage_count": int(shortage_count),
                "risk_score": float(shortage_count * passing_bay_shortage_risk),
            }
        )
    return risks


def _directionality_node_issues(graph, reachable_from_entries: set[str], can_reach_exits: set[str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for node in graph.nodes:
        if node.kind == "entrance":
            continue
        reachable = node.id in reachable_from_entries
        can_exit = node.id in can_reach_exits
        issue = None
        if reachable and not can_exit:
            issue = "one_way_trap"
        elif can_exit and not reachable:
            issue = "exit_only_fragment"
        elif not reachable and not can_exit:
            issue = "isolated_directional_fragment"
        if issue is None:
            continue
        issues.append(
            {
                "node_id": node.id,
                "aisle_id": node.ref_id,
                "node_kind": node.kind,
                "issue": issue,
                "reachable_from_entry": reachable,
                "can_reach_exit": can_exit,
            }
        )
    return issues


def _directionality_stall_issues(stall_access, node_issue_by_id: dict[str, dict[str, Any]], issue_risk: float) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for access in stall_access:
        if not access.aisle_node_id or access.aisle_node_id not in node_issue_by_id:
            continue
        node_issue = node_issue_by_id[access.aisle_node_id]
        issues.append(
            {
                "stall_id": access.stall_id,
                "aisle_id": access.aisle_id,
                "aisle_node_id": access.aisle_node_id,
                "issue": f"stall_on_{node_issue['issue']}",
                "node_issue": node_issue["issue"],
                "risk_score": float(issue_risk),
            }
        )
    return issues


def _directionality_summary(node_issues: list[dict[str, Any]], stall_issues: list[dict[str, Any]], checked_stall_count: int) -> dict[str, Any]:
    node_issue_counts = _issue_counts(node_issues)
    stall_issue_counts = _issue_counts(stall_issues)
    stall_issue_count = len(stall_issues)
    return {
        "checked_stall_count": checked_stall_count,
        "node_issue_count": len(node_issues),
        "stall_issue_count": stall_issue_count,
        "stall_issue_ratio": float(stall_issue_count / checked_stall_count) if checked_stall_count else 0.0,
        "one_way_trap_node_count": node_issue_counts.get("one_way_trap", 0),
        "exit_only_fragment_node_count": node_issue_counts.get("exit_only_fragment", 0),
        "isolated_directional_fragment_node_count": node_issue_counts.get("isolated_directional_fragment", 0),
        "stall_on_one_way_trap_count": stall_issue_counts.get("stall_on_one_way_trap", 0),
        "stall_on_exit_only_fragment_count": stall_issue_counts.get("stall_on_exit_only_fragment", 0),
        "stall_on_isolated_directional_fragment_count": stall_issue_counts.get("stall_on_isolated_directional_fragment", 0),
        "node_issue_counts": node_issue_counts,
        "stall_issue_counts": stall_issue_counts,
    }


def _directionality_summary_risks(
    summary: dict[str, Any],
    max_issue_ratio: float | None,
    issue_ratio_risk: float,
) -> list[dict[str, Any]]:
    ratio = summary.get("stall_issue_ratio")
    if (
        max_issue_ratio is not None
        and isinstance(ratio, int | float)
        and float(ratio) > max_issue_ratio + 1e-9
    ):
        return [
            {
                "id": "OQ-DIRECTIONALITY-STALL-ISSUE-RATIO",
                "issue": "directionality_stall_issue_ratio_exceeds_limit",
                "stall_issue_ratio": float(ratio),
                "max_directionality_issue_ratio": max_issue_ratio,
                "stall_issue_count": summary["stall_issue_count"],
                "checked_stall_count": summary["checked_stall_count"],
                "risk_score": float(issue_ratio_risk),
            }
        ]
    return []


def _issue_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        issue = item.get("issue")
        if issue is None:
            continue
        counts[str(issue)] = counts.get(str(issue), 0) + 1
    return counts


def _selected_aisle_class(site: SiteSpec):
    if site.fixed_aisle_class:
        for aisle_class in site.aisle_classes:
            if aisle_class.id == site.fixed_aisle_class:
                return aisle_class
    for aisle_class in site.aisle_classes:
        if aisle_class.enabled:
            return aisle_class
    return None


def _is_narrow_two_way_class(aisle_class) -> bool:
    return bool(
        aisle_class
        and aisle_class.capacity == "single_vehicle"
        and aisle_class.directionality == "two_way"
    )


def _passing_bay_markers(site: SiteSpec) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for index, feature in enumerate(site.site_features, start=1):
        if not isinstance(feature, dict):
            continue
        raw_type = feature.get("type") or feature.get("feature_type") or feature.get("kind")
        feature_type = _normalized_feature_type(raw_type)
        if feature_type not in _PASSING_BAY_FEATURE_TYPES:
            continue
        marker: dict[str, Any] = {
            "id": str(feature.get("id") or f"passing-bay-{index:03d}"),
            "type": feature_type,
        }
        for key in ("aisle_id", "side", "center", "width", "length", "geometry"):
            if key in feature:
                marker[key] = feature[key]
        markers.append(marker)
    return markers


def _passing_bay_reports(
    markers: list[dict[str, Any]],
    aisles: list[ParkingAisle],
    touch_tolerance: float,
    min_area: float | None,
    issue_risk: float,
) -> list[dict[str, Any]]:
    aisle_polygons = [(aisle, ShapelyPolygon(aisle.polygon)) for aisle in aisles]
    reports: list[dict[str, Any]] = []
    for marker in markers:
        geometry, geometry_source = _passing_bay_geometry(marker)
        area = float(geometry.area) if geometry is not None else None
        center = _passing_bay_center(marker, geometry)
        associated_aisle, aisle_distance, aisle_overlap_area = _associated_passing_bay_aisle(
            marker,
            geometry,
            center,
            aisle_polygons,
            touch_tolerance,
        )
        issues: list[str] = []
        if geometry is None:
            issues.append("passing_bay_geometry_missing")
        if associated_aisle is None:
            issues.append("passing_bay_not_associated_with_narrow_two_way_aisle")
        elif aisle_distance is not None and aisle_distance > touch_tolerance + 1e-9:
            issues.append("passing_bay_not_adjacent_to_aisle")
        if min_area is not None and area is not None and area + 1e-9 < min_area:
            issues.append("passing_bay_area_below_minimum")
        usable = not issues
        reports.append(
            {
                **marker,
                "geometry_available": geometry is not None,
                "geometry_source": geometry_source,
                "area": area,
                "center": list(center) if center is not None else None,
                "associated_aisle_id": associated_aisle.id if associated_aisle else None,
                "distance_to_associated_aisle": aisle_distance,
                "aisle_overlap_area": aisle_overlap_area,
                "usable": usable,
                "issues": issues,
                "risk_score": float(issue_risk if issues else 0.0),
            }
        )
    return reports


def _passing_bay_geometry(marker: dict[str, Any]):
    geometry = marker.get("geometry")
    if isinstance(geometry, dict):
        parsed = _feature_geometry_polygon(geometry)
        if parsed is not None and parsed.area > 1e-9:
            return parsed, str(geometry.get("type", "geometry"))
    center = _raw_point(marker.get("center"))
    width = _optional_positive_float(marker.get("width"))
    length = _optional_positive_float(marker.get("length") if "length" in marker else marker.get("height"))
    if center is not None and width is not None and length is not None:
        cx, cy = center
        polygon = ShapelyPolygon(
            [
                (cx - width / 2, cy - length / 2),
                (cx + width / 2, cy - length / 2),
                (cx + width / 2, cy + length / 2),
                (cx - width / 2, cy + length / 2),
            ]
        )
        return polygon, "center_width_length"
    return None, None


def _feature_geometry_polygon(geometry: dict[str, Any]):
    geometry_type = _normalized_feature_type(geometry.get("type"))
    if geometry_type == "polygon":
        points = [_raw_point(item) for item in geometry.get("points", [])]
        clean_points = [item for item in points if item is not None]
        if len(clean_points) >= 3:
            return ShapelyPolygon(clean_points)
        return None
    if geometry_type == "rectangle":
        origin = _raw_point(geometry.get("origin"))
        width = _optional_positive_float(geometry.get("width"))
        height = _optional_positive_float(geometry.get("height") if "height" in geometry else geometry.get("length"))
        if origin is None or width is None or height is None:
            return None
        polygon = ShapelyPolygon([(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)])
        polygon = affinity.rotate(
            polygon,
            float(geometry.get("rotation_degrees", 0.0)),
            origin=(0.0, 0.0),
            use_radians=False,
        )
        return affinity.translate(polygon, xoff=origin[0], yoff=origin[1])
    if geometry_type == "circle":
        center = _raw_point(geometry.get("center"))
        radius = _optional_positive_float(geometry.get("radius"))
        if center is None or radius is None:
            return None
        return ShapelyPoint(center).buffer(radius)
    if geometry_type == "polyline_buffer":
        points = [_raw_point(item) for item in geometry.get("points", [])]
        clean_points = [item for item in points if item is not None]
        width = _optional_positive_float(geometry.get("width"))
        if len(clean_points) < 2 or width is None:
            return None
        return LineString(clean_points).buffer(width / 2, cap_style="flat", join_style="mitre")
    return None


def _passing_bay_center(marker: dict[str, Any], geometry) -> tuple[float, float] | None:
    explicit = _raw_point(marker.get("center"))
    if explicit is not None:
        return explicit
    if geometry is not None:
        centroid = geometry.centroid
        return (float(centroid.x), float(centroid.y))
    return None


def _associated_passing_bay_aisle(
    marker: dict[str, Any],
    geometry,
    center: tuple[float, float] | None,
    aisle_polygons,
    touch_tolerance: float,
):
    if not aisle_polygons:
        return None, None, None
    marker_aisle_id = marker.get("aisle_id")
    candidates = [
        item
        for item in aisle_polygons
        if marker_aisle_id is not None and item[0].id == str(marker_aisle_id)
    ]
    if not candidates:
        candidates = aisle_polygons
    target = geometry if geometry is not None else ShapelyPoint(center) if center is not None else None
    if target is None:
        if marker_aisle_id is not None and candidates:
            return candidates[0][0], None, None
        return None, None, None
    measured = [
        (
            aisle,
            float(target.distance(polygon)),
            float(target.intersection(polygon).area) if geometry is not None else 0.0,
        )
        for aisle, polygon in candidates
    ]
    if not measured:
        return None, None, None
    aisle, distance, overlap_area = min(measured, key=lambda item: item[1])
    if marker_aisle_id is None and distance > touch_tolerance + 1e-9:
        return None, distance, overlap_area
    return aisle, distance, overlap_area


def _normalized_feature_type(raw: object) -> str:
    if raw is None:
        return ""
    return str(raw).strip().lower().replace("-", "_")


def _aisle_class_report(aisle_class) -> dict[str, Any] | None:
    if aisle_class is None:
        return None
    return {
        "id": aisle_class.id,
        "width": aisle_class.width,
        "capacity": aisle_class.capacity,
        "directionality": aisle_class.directionality,
        "centerline_crossing": aisle_class.centerline_crossing,
        "enabled": aisle_class.enabled,
    }


def _route_summary(routes: list[dict[str, Any]]) -> dict[str, Any]:
    finite_routes = [
        route
        for route in routes
        if isinstance(route.get("route_length"), int | float)
    ]
    issue_counts: dict[str, int] = {}
    for route in routes:
        issues = route.get("issues", [])
        if not isinstance(issues, list):
            continue
        for issue in issues:
            issue_counts[str(issue)] = issue_counts.get(str(issue), 0) + 1
    turnaround_dependency_count = len(
        [
            route
            for route in routes
            if bool(route.get("depends_on_dead_end_turnaround"))
        ]
    )
    long_route_count = issue_counts.get("route_length_exceeds_limit", 0)
    return {
        "checked_stall_count": len(routes),
        "route_with_length_count": len(finite_routes),
        "average_route_length": _average_route_length(finite_routes),
        "max_route_length": _max_route_length(finite_routes),
        "max_entry_path_length": _max_path_length(routes, "entry_path_length"),
        "max_exit_path_length": _max_path_length(routes, "exit_path_length"),
        "longest_route_stall_id": _longest_route_stall_id(finite_routes),
        "turnaround_dependency_count": turnaround_dependency_count,
        "turnaround_dependency_ratio": (
            float(turnaround_dependency_count / len(routes))
            if routes
            else 0.0
        ),
        "missing_entry_path_count": issue_counts.get("missing_entry_path", 0),
        "missing_exit_path_count": issue_counts.get("missing_exit_path", 0),
        "route_length_exceeds_limit_count": issue_counts.get("route_length_exceeds_limit", 0),
        "long_route_ratio": float(long_route_count / len(routes)) if routes else 0.0,
        "depends_on_dead_end_turnaround_count": issue_counts.get("depends_on_dead_end_turnaround", 0),
        "issue_counts": issue_counts,
    }


def _route_summary_risks(
    summary: dict[str, Any],
    max_turnaround_dependency_ratio: float | None,
    turnaround_dependency_ratio_risk: float,
    max_average_route_length: float | None,
    average_route_length_risk: float,
    max_long_route_ratio: float | None,
    long_route_ratio_risk: float,
) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    ratio = summary.get("turnaround_dependency_ratio")
    if (
        max_turnaround_dependency_ratio is not None
        and isinstance(ratio, int | float)
        and float(ratio) > max_turnaround_dependency_ratio + 1e-9
    ):
        risks.append(
            {
                "id": "OQ-ROUTE-SUMMARY-TURNAROUND-RATIO",
                "issue": "turnaround_dependency_ratio_exceeds_limit",
                "turnaround_dependency_ratio": float(ratio),
                "max_turnaround_dependency_ratio": max_turnaround_dependency_ratio,
                "turnaround_dependency_count": summary["turnaround_dependency_count"],
                "checked_stall_count": summary["checked_stall_count"],
                "risk_score": float(turnaround_dependency_ratio_risk),
            }
        )
    average_route_length = summary.get("average_route_length")
    if (
        max_average_route_length is not None
        and isinstance(average_route_length, int | float)
        and float(average_route_length) > max_average_route_length + 1e-9
    ):
        risks.append(
            {
                "id": "OQ-ROUTE-SUMMARY-AVERAGE-LENGTH",
                "issue": "average_route_length_exceeds_limit",
                "average_route_length": float(average_route_length),
                "max_average_route_length": max_average_route_length,
                "checked_stall_count": summary["checked_stall_count"],
                "risk_score": float(average_route_length_risk),
            }
        )
    long_route_ratio = summary.get("long_route_ratio")
    if (
        max_long_route_ratio is not None
        and isinstance(long_route_ratio, int | float)
        and float(long_route_ratio) > max_long_route_ratio + 1e-9
    ):
        risks.append(
            {
                "id": "OQ-ROUTE-SUMMARY-LONG-ROUTE-RATIO",
                "issue": "long_route_ratio_exceeds_limit",
                "long_route_ratio": float(long_route_ratio),
                "max_long_route_ratio": max_long_route_ratio,
                "route_length_exceeds_limit_count": summary["route_length_exceeds_limit_count"],
                "checked_stall_count": summary["checked_stall_count"],
                "risk_score": float(long_route_ratio_risk),
            }
        )
    return risks


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


def _reachable_node_ids(edges, starts: set[str], reverse: bool) -> set[str]:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
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
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(sorted(adjacency.get(node_id, set()) - seen))
    return seen


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


def _add_arc(adjacency: dict[str, set[str]], source: str, target: str) -> None:
    adjacency.setdefault(source, set()).add(target)


def _finite_distance(value: float | None) -> float | None:
    return None if value is None else float(value)


def _average_route_length(routes: list[dict[str, Any]]) -> float | None:
    if not routes:
        return None
    return float(sum(float(route["route_length"]) for route in routes) / len(routes))


def _max_route_length(routes: list[dict[str, Any]]) -> float | None:
    if not routes:
        return None
    return float(max(float(route["route_length"]) for route in routes))


def _max_path_length(routes: list[dict[str, Any]], key: str) -> float | None:
    values = [
        float(route[key])
        for route in routes
        if isinstance(route.get(key), int | float)
    ]
    return max(values) if values else None


def _longest_route_stall_id(routes: list[dict[str, Any]]) -> str | None:
    if not routes:
        return None
    longest = max(routes, key=lambda item: float(item["route_length"]))
    stall_id = longest.get("stall_id")
    return str(stall_id) if stall_id is not None else None


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


def _optional_positive_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _optional_nonnegative_int(raw: object) -> int | None:
    value = _optional_nonnegative_float(raw)
    if value is None:
        return None
    return int(value)


def _raw_point(raw: object) -> tuple[float, float] | None:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        return None
    try:
        return (float(raw[0]), float(raw[1]))
    except (TypeError, ValueError):
        return None


def _optional_ratio(raw: object) -> float | None:
    value = _optional_nonnegative_float(raw)
    if value is None:
        return None
    return min(value, 1.0)


def _blocking_conflicts(
    junctions: list[dict[str, Any]],
    entrance_throats: list[dict[str, Any]],
    route_risks: dict[str, Any],
    directionality_risks: dict[str, Any],
    narrow_two_way_risks: dict[str, Any],
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
    for summary_risk in _route_summary_conflicts(route_risks):
        conflicts.append(summary_risk)
    for directionality in _directionality_conflicts(directionality_risks):
        conflicts.append(directionality)
    for narrow_two_way in _narrow_two_way_conflicts(narrow_two_way_risks):
        conflicts.append(narrow_two_way)
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


def _route_summary_conflicts(route_risks: dict[str, Any]) -> list[dict[str, Any]]:
    raw = route_risks.get("summary_risks", [])
    if not isinstance(raw, list):
        return []
    conflicts = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        conflict = {
            "source_type": "route_summary",
            "source_id": item["id"],
            "issue": item["issue"],
            "risk_score": item["risk_score"],
        }
        for key in (
            "turnaround_dependency_ratio",
            "max_turnaround_dependency_ratio",
            "turnaround_dependency_count",
            "average_route_length",
            "max_average_route_length",
            "long_route_ratio",
            "max_long_route_ratio",
            "route_length_exceeds_limit_count",
            "checked_stall_count",
        ):
            if key in item:
                conflict[key] = item[key]
        conflicts.append(conflict)
    return conflicts


def _directionality_conflicts(directionality_risks: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    raw_stalls = directionality_risks.get("stall_issues", [])
    if isinstance(raw_stalls, list):
        for item in raw_stalls:
            if not isinstance(item, dict) or float(item.get("risk_score", 0.0)) <= 0:
                continue
            conflicts.append(
                {
                    "source_type": "directionality_stall",
                    "source_id": f"OQ-DIRECTIONALITY-{item['stall_id']}",
                    "stall_id": item["stall_id"],
                    "aisle_id": item["aisle_id"],
                    "aisle_node_id": item["aisle_node_id"],
                    "issue": item["issue"],
                    "node_issue": item["node_issue"],
                    "risk_score": item["risk_score"],
                }
            )
    raw_summary = directionality_risks.get("summary_risks", [])
    if isinstance(raw_summary, list):
        for item in raw_summary:
            if not isinstance(item, dict):
                continue
            conflicts.append(
                {
                    "source_type": "directionality_summary",
                    "source_id": item["id"],
                    "issue": item["issue"],
                    "stall_issue_ratio": item["stall_issue_ratio"],
                    "max_directionality_issue_ratio": item["max_directionality_issue_ratio"],
                    "stall_issue_count": item["stall_issue_count"],
                    "checked_stall_count": item["checked_stall_count"],
                    "risk_score": item["risk_score"],
                }
            )
    return conflicts


def _narrow_two_way_conflicts(narrow_two_way_risks: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    raw_passing_bays = narrow_two_way_risks.get("passing_bays", [])
    if isinstance(raw_passing_bays, list):
        for item in raw_passing_bays:
            if not isinstance(item, dict) or float(item.get("risk_score", 0.0)) <= 0:
                continue
            conflicts.append(
                {
                    "source_type": "passing_bay",
                    "source_id": f"OQ-PASSING-BAY-{item['id']}",
                    "passing_bay_id": item["id"],
                    "associated_aisle_id": item.get("associated_aisle_id"),
                    "usable": item.get("usable"),
                    "issues": list(item.get("issues", [])),
                    "risk_score": item["risk_score"],
                }
            )
    raw_stalls = narrow_two_way_risks.get("stall_issues", [])
    if isinstance(raw_stalls, list):
        for item in raw_stalls:
            if not isinstance(item, dict) or float(item.get("risk_score", 0.0)) <= 0:
                continue
            conflicts.append(
                {
                    "source_type": "narrow_two_way_stall",
                    "source_id": f"OQ-NARROW-TWO-WAY-{item['stall_id']}",
                    "stall_id": item["stall_id"],
                    "aisle_id": item["aisle_id"],
                    "issue": item["issue"],
                    "risk_score": item["risk_score"],
                }
            )
    raw_summary = narrow_two_way_risks.get("summary_risks", [])
    if isinstance(raw_summary, list):
        for item in raw_summary:
            if not isinstance(item, dict):
                continue
            conflict = {
                "source_type": "narrow_two_way_summary",
                "source_id": item["id"],
                "issue": item["issue"],
                "risk_score": item["risk_score"],
            }
            for key in (
                "affected_stall_ratio",
                "max_narrow_two_way_stall_ratio",
                "affected_stall_count",
                "checked_stall_count",
                "passing_bay_marker_count",
                "usable_passing_bay_count",
                "min_passing_bays",
                "passing_bay_shortage_count",
            ):
                if key in item:
                    conflict[key] = item[key]
            conflicts.append(conflict)
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
