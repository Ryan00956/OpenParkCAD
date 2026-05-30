from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from shapely.geometry import LineString, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import area_overlaps, available_area, polygon_points
from openparkcad.models import LayoutResult, ParkingStall, SiteSpec, StallSpec


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
                    "stall_type_id": stall.stall_type_id,
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
        min_ratio = float(envelope_result["minimum_coverage_ratio"])
        min_turn_ratio = float(envelope_result["minimum_turn_coverage_ratio"])
        record = {
            "stall_id": stall.id,
            "stall_type_id": stall.stall_type_id,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "rule_id": rule.id,
            "rule_status": rule.status,
            "drivable_coverage_ratio": drivable_ratio,
            "usable_coverage_ratio": usable_ratio,
            "turn_drivable_coverage_ratio": turn_drivable_ratio,
            "turn_usable_coverage_ratio": turn_usable_ratio,
            "depth": float(envelope_result["access_depth"]),
            "turn_buffer_length": float(envelope_result["turn_buffer_length"]),
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
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=stall_spec.width,
        access_depth=_access_depth(site),
        turn_buffer_length=_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_turn_coverage_ratio(site),
    )


def _angled_access_envelope(context: ManeuverContext, stall: ParkingStall) -> dict[str, Any]:
    site = context.site
    stall_spec = _stall_spec_for_stall(site, stall)
    return _front_access_envelope(
        context,
        stall,
        target_edge_width=_angled_front_edge_width(stall_spec),
        access_depth=_angled_access_depth(site, stall_spec),
        turn_buffer_length=_angled_turn_buffer_length(site, stall_spec),
        minimum_coverage_ratio=_minimum_angled_coverage_ratio(site),
        minimum_turn_coverage_ratio=_minimum_angled_turn_coverage_ratio(site),
    )


def _front_access_envelope(
    context: ManeuverContext,
    stall: ParkingStall,
    target_edge_width: float,
    access_depth: float,
    turn_buffer_length: float,
    minimum_coverage_ratio: float,
    minimum_turn_coverage_ratio: float,
) -> dict[str, Any]:
    site = context.site
    if not stall.served_by_aisle_id:
        return {"envelope": None, "reason": "stall_has_no_serving_aisle"}
    aisle = context.aisle_by_id.get(stall.served_by_aisle_id)
    if aisle is None:
        return {"envelope": None, "reason": "serving_aisle_missing"}

    stall_polygon = ShapelyPolygon(stall.polygon)
    edge = _front_edge(stall_polygon, aisle, target_edge_width)
    if edge is None:
        return {"envelope": None, "reason": "front_access_edge_not_found"}

    envelope = _edge_access_rectangle(edge, stall_polygon, aisle, access_depth, extra_along_edge=0.0)
    if envelope is None or envelope.area <= 1e-9:
        return {"envelope": None, "reason": "access_envelope_not_possible"}
    turn_proxy = _edge_access_rectangle(
        edge,
        stall_polygon,
        aisle,
        access_depth,
        extra_along_edge=turn_buffer_length,
    )
    if turn_proxy is None or turn_proxy.area <= 1e-9:
        return {"envelope": None, "reason": "turning_sweep_not_possible"}
    return {
        "envelope": envelope,
        "turn_proxy": turn_proxy,
        "access_depth": access_depth,
        "turn_buffer_length": turn_buffer_length,
        "minimum_coverage_ratio": minimum_coverage_ratio,
        "minimum_turn_coverage_ratio": minimum_turn_coverage_ratio,
        "reason": "ok",
    }


def _unsupported_maneuver_rule(reason: str) -> Callable[[ManeuverContext, ParkingStall], dict[str, Any]]:
    def evaluate(_context: ManeuverContext, _stall: ParkingStall) -> dict[str, Any]:
        return {"envelope": None, "reason": reason}

    return evaluate


def _rule_for_stall(site: SiteSpec, stall: ParkingStall) -> ManeuverRule:
    stall_spec = _stall_spec_for_stall(site, stall)
    family = stall_spec.family
    if family == "perpendicular" and _angle_supported_for_perpendicular(stall_spec, stall):
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
            id="angled_proxy",
            family=family,
            status="active",
            evaluate=_angled_access_envelope,
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
    active_families = {stall.family for stall in _active_stall_specs(site)}
    return {
        "perpendicular_90_proxy": "active" if "perpendicular" in active_families else "available",
        "perpendicular_non_90": "future",
        "angled_proxy": "active" if "angled" in active_families else "available",
        "parallel": "future",
        "t_end": "future",
    }


def _angle_supported_for_perpendicular(stall_spec: StallSpec, stall: ParkingStall) -> bool:
    _ = stall
    if not _angle_allowed(90.0, stall_spec.allowed_angles):
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


def _angled_access_depth(site: SiteSpec, stall_spec: StallSpec) -> float:
    angle = _active_angled_angle(stall_spec)
    default = site.aisle_width * max(math.sin(math.radians(angle)), 0.5)
    raw = site.optimization.get("maneuver_angled_access_depth", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _angled_front_edge_width(stall_spec: StallSpec) -> float:
    angle = _active_angled_angle(stall_spec)
    return stall_spec.width / max(math.sin(math.radians(angle)), 1e-6)


def _turn_buffer_length(site: SiteSpec, stall_spec: StallSpec | None = None) -> float:
    active_stall = stall_spec or site.stall
    default = min(site.aisle_width / 2, active_stall.width)
    if site.vehicle:
        default = min(site.aisle_width / 2, max(site.vehicle.width, active_stall.width) + site.vehicle.swept_path_margin)
    raw = site.optimization.get("maneuver_turn_buffer_length", default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)


def _angled_turn_buffer_length(site: SiteSpec, stall_spec: StallSpec) -> float:
    default = min(_turn_buffer_length(site, stall_spec), stall_spec.width / 2)
    raw = site.optimization.get("maneuver_angled_turn_buffer_length", default)
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


def _minimum_angled_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_angled_access_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _minimum_turn_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_turn_coverage_ratio", _minimum_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _minimum_angled_turn_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_angled_turn_coverage_ratio", _minimum_turn_coverage_ratio(site))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _minimum_turn_coverage_ratio(site)
    return min(max(value, 0.0), 1.0)


def _active_angled_angle(stall_spec: StallSpec) -> float:
    for angle in stall_spec.allowed_angles:
        normalized = angle % 180
        if 1e-6 < normalized < 180 - 1e-6 and abs(normalized - 90.0) > 1e-6:
            return normalized
    return 60.0


def _renumber_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=polygon_points(ShapelyPolygon(stall.polygon)),
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
            stall_type_id=stall.stall_type_id,
        )
        for index, stall in enumerate(stalls, start=1)
    ]


def _stall_spec_for_stall(site: SiteSpec, stall: ParkingStall) -> StallSpec:
    active = {spec.id: spec for spec in _active_stall_specs(site)}
    if stall.stall_type_id and stall.stall_type_id in active:
        return active[stall.stall_type_id]
    return site.stall


def _active_stall_specs(site: SiteSpec) -> tuple[StallSpec, ...]:
    specs: list[StallSpec] = []
    for spec in (site.main_stall, site.branch_stall, site.stall, *site.stall_candidates):
        if spec is not None and spec.id not in {item.id for item in specs}:
            specs.append(spec)
    return tuple(specs)
