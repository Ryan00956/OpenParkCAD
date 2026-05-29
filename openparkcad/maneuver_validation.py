from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import area_overlaps, available_area, polygon_points
from openparkcad.models import LayoutResult, ParkingStall, SiteSpec


@dataclass(frozen=True)
class ManeuverContext:
    site: SiteSpec
    aisle_by_id: dict[str, ShapelyPolygon]
    drivable: Any
    usable: Any


@dataclass(frozen=True)
class ManeuverRule:
    id: str
    family: str
    status: str
    evaluate: Callable[[ManeuverContext, ParkingStall], dict[str, Any]]


def apply_maneuver_filter(layout: LayoutResult) -> LayoutResult:
    validation = validate_maneuvers(layout)
    invalid_ids = {item["stall_id"] for item in validation["invalid_stalls"]}
    if not invalid_ids:
        object.__setattr__(layout, "maneuver_validation", validation)
        return layout

    kept_stalls = [stall for stall in layout.stalls if stall.id not in invalid_ids]
    object.__setattr__(layout, "stalls", _renumber_stalls(kept_stalls))
    post_validation = validate_maneuvers(layout)
    post_validation["filtered_stall_ids"] = sorted(invalid_ids)
    post_validation["filtered_stall_count"] = len(invalid_ids)
    post_validation["pre_filter_invalid_stalls"] = validation["invalid_stalls"]
    object.__setattr__(layout, "maneuver_validation", post_validation)
    return layout


def validate_maneuvers(layout: LayoutResult) -> dict[str, Any]:
    aisle_by_id = {aisle.id: ShapelyPolygon(aisle.polygon) for aisle in layout.aisles}
    drivable = unary_union(list(aisle_by_id.values())) if aisle_by_id else ShapelyPolygon()
    usable = available_area(layout.site)
    context = ManeuverContext(
        site=layout.site,
        aisle_by_id=aisle_by_id,
        drivable=drivable,
        usable=usable,
    )
    invalid: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    rule_counts: dict[str, int] = {}

    for stall in layout.stalls:
        rule = _rule_for_stall(layout.site, stall)
        rule_counts[rule.id] = rule_counts.get(rule.id, 0) + 1
        envelope_result = rule.evaluate(context, stall)
        if envelope_result["envelope"] is None:
            invalid.append(
                {
                    "stall_id": stall.id,
                    "served_by_aisle_id": stall.served_by_aisle_id,
                    "rule_id": rule.id,
                    "rule_status": rule.status,
                    "reason": envelope_result["reason"],
                }
            )
            continue

        envelope = envelope_result["envelope"]
        turn_proxy = envelope_result["turn_proxy"]
        drivable_ratio = _coverage_ratio(envelope, drivable)
        usable_ratio = _coverage_ratio(envelope, usable)
        turn_drivable_ratio = _coverage_ratio(turn_proxy, drivable)
        turn_usable_ratio = _coverage_ratio(turn_proxy, usable)
        min_ratio = _minimum_coverage_ratio(layout.site)
        min_turn_ratio = _minimum_turn_coverage_ratio(layout.site)
        record = {
            "stall_id": stall.id,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "rule_id": rule.id,
            "rule_status": rule.status,
            "drivable_coverage_ratio": drivable_ratio,
            "usable_coverage_ratio": usable_ratio,
            "turn_drivable_coverage_ratio": turn_drivable_ratio,
            "turn_usable_coverage_ratio": turn_usable_ratio,
            "depth": _access_depth(layout.site),
            "turn_buffer_length": _turn_buffer_length(layout.site),
        }
        envelopes.append(record)
        if drivable_ratio + 1e-9 < min_ratio:
            invalid.append({**record, "reason": "access_envelope_not_in_drivable_aisle"})
        elif usable_ratio + 1e-9 < min_ratio:
            invalid.append({**record, "reason": "access_envelope_hits_boundary_or_obstacle"})
        elif turn_drivable_ratio + 1e-9 < min_turn_ratio:
            invalid.append({**record, "reason": "turning_sweep_not_in_drivable_aisle"})
        elif turn_usable_ratio + 1e-9 < min_turn_ratio:
            invalid.append({**record, "reason": "turning_sweep_hits_boundary_or_obstacle"})

    return {
        "valid": not invalid,
        "checked_stalls": len(layout.stalls),
        "invalid_stalls": invalid,
        "access_depth": _access_depth(layout.site),
        "minimum_coverage_ratio": _minimum_coverage_ratio(layout.site),
        "turn_buffer_length": _turn_buffer_length(layout.site),
        "minimum_turn_coverage_ratio": _minimum_turn_coverage_ratio(layout.site),
        "rule_counts": rule_counts,
        "rule_support": _rule_support_report(layout.site),
        "envelopes": envelopes,
    }


def _perpendicular_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    site = context.site
    if not stall.served_by_aisle_id:
        return {"envelope": None, "reason": "stall_has_no_serving_aisle"}
    aisle = context.aisle_by_id.get(stall.served_by_aisle_id)
    if aisle is None:
        return {"envelope": None, "reason": "serving_aisle_missing"}

    stall_polygon = ShapelyPolygon(stall.polygon)
    edge = _front_edge(stall_polygon, aisle, site.stall.width)
    if edge is None:
        return {"envelope": None, "reason": "front_access_edge_not_found"}

    envelope = _edge_access_rectangle(edge, stall_polygon, aisle, _access_depth(site), extra_along_edge=0.0)
    if envelope is None or envelope.area <= 1e-9:
        return {"envelope": None, "reason": "access_envelope_not_possible"}
    turn_proxy = _edge_access_rectangle(
        edge,
        stall_polygon,
        aisle,
        _access_depth(site),
        extra_along_edge=_turn_buffer_length(site),
    )
    if turn_proxy is None or turn_proxy.area <= 1e-9:
        return {"envelope": None, "reason": "turning_sweep_not_possible"}
    return {"envelope": envelope, "turn_proxy": turn_proxy, "reason": "ok"}


def _unsupported_maneuver_rule(reason: str) -> Callable[[ManeuverContext, ParkingStall], dict[str, Any]]:
    def evaluate(_context: ManeuverContext, _stall: ParkingStall) -> dict[str, Any]:
        return {"envelope": None, "reason": reason}

    return evaluate


def _rule_for_stall(site: SiteSpec, stall: ParkingStall) -> ManeuverRule:
    family = site.stall.family
    if family == "perpendicular" and _angle_supported_for_perpendicular(site, stall):
        return ManeuverRule(
            id="perpendicular_90_proxy",
            family=family,
            status="active",
            evaluate=_perpendicular_access_envelope,
        )
    if family == "perpendicular":
        return ManeuverRule(
            id="perpendicular_non_90_future",
            family=family,
            status="future",
            evaluate=_unsupported_maneuver_rule("perpendicular_non_90_maneuver_rule_not_implemented"),
        )
    if family == "angled":
        return ManeuverRule(
            id="angled_future",
            family=family,
            status="future",
            evaluate=_unsupported_maneuver_rule("angled_maneuver_rule_not_implemented"),
        )
    if family == "parallel":
        return ManeuverRule(
            id="parallel_future",
            family=family,
            status="future",
            evaluate=_unsupported_maneuver_rule("parallel_maneuver_rule_not_implemented"),
        )
    if family == "t_end":
        return ManeuverRule(
            id="t_end_future",
            family=family,
            status="future",
            evaluate=_unsupported_maneuver_rule("t_end_maneuver_rule_not_implemented"),
        )
    return ManeuverRule(
        id="unknown_family_future",
        family=family,
        status="future",
        evaluate=_unsupported_maneuver_rule("stall_family_maneuver_rule_not_implemented"),
    )


def _rule_support_report(site: SiteSpec) -> dict[str, str]:
    return {
        "perpendicular_90_proxy": "active" if site.stall.family == "perpendicular" else "available",
        "perpendicular_non_90": "future",
        "angled": "future",
        "parallel": "future",
        "t_end": "future",
    }


def _angle_supported_for_perpendicular(site: SiteSpec, stall: ParkingStall) -> bool:
    _ = stall
    if not _angle_allowed(90.0, site.stall.allowed_angles):
        return False
    return True


def _angle_allowed(angle: float, allowed_angles: tuple[float, ...]) -> bool:
    normalized = angle % 180
    return any(abs(normalized - (allowed % 180)) <= 1e-6 for allowed in allowed_angles)


def _front_edge(stall: ShapelyPolygon, aisle: ShapelyPolygon, target_width: float) -> LineString | None:
    coords = list(stall.exterior.coords)
    candidates: list[tuple[float, float, float, LineString]] = []
    for start, end in zip(coords, coords[1:]):
        edge = LineString([start, end])
        length = edge.length
        distance = edge.distance(aisle)
        if length <= 1e-9:
            continue
        if distance <= 1e-5 or area_overlaps(edge.buffer(1e-5, cap_style=2), aisle):
            candidates.append((distance, abs(length - target_width), -length, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _edge_access_rectangle(
    edge: LineString,
    stall: ShapelyPolygon,
    aisle: ShapelyPolygon,
    depth: float,
    extra_along_edge: float,
) -> ShapelyPolygon | None:
    coords = list(edge.coords)
    if len(coords) < 2 or depth <= 0:
        return None
    p1 = coords[0]
    p2 = coords[-1]
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return None

    normals = [(-dy / length, dx / length), (dy / length, -dx / length)]
    midpoint = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    aisle_center = aisle.centroid
    stall_center = stall.centroid
    target = (aisle_center.x - midpoint[0], aisle_center.y - midpoint[1])
    away_from_stall = (midpoint[0] - stall_center.x, midpoint[1] - stall_center.y)

    normal = max(
        normals,
        key=lambda item: (
            item[0] * target[0] + item[1] * target[1],
            item[0] * away_from_stall[0] + item[1] * away_from_stall[1],
        ),
    )
    tangent = (dx / length, dy / length)
    extra = max(extra_along_edge, 0.0)
    q1 = (p1[0] - tangent[0] * extra, p1[1] - tangent[1] * extra)
    q2 = (p2[0] + tangent[0] * extra, p2[1] + tangent[1] * extra)
    q3 = (q2[0] + normal[0] * depth, q2[1] + normal[1] * depth)
    q4 = (q1[0] + normal[0] * depth, q1[1] + normal[1] * depth)
    return ShapelyPolygon([q1, q2, q3, q4])


def _coverage_ratio(envelope: ShapelyPolygon, area) -> float:
    if envelope.area <= 1e-9:
        return 0.0
    return min(envelope.intersection(area).area / envelope.area, 1.0)


def _access_depth(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_access_depth", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


def _turn_buffer_length(site: SiteSpec) -> float:
    default = min(site.aisle_width / 2, site.stall.width)
    if site.vehicle:
        default = min(site.aisle_width / 2, max(site.vehicle.width, site.stall.width) + site.vehicle.swept_path_margin)
    raw = site.optimization.get("maneuver_turn_buffer_length", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _minimum_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_access_coverage_ratio", 0.95)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.95
    return min(max(value, 0.0), 1.0)


def _minimum_turn_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_turn_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _renumber_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=polygon_points(ShapelyPolygon(stall.polygon)),
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
        )
        for index, stall in enumerate(stalls, start=1)
    ]
