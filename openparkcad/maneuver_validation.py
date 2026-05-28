from __future__ import annotations

import math
from typing import Any

from shapely.geometry import LineString, Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import area_overlaps, available_area, polygon_points
from openparkcad.models import LayoutResult, ParkingStall, SiteSpec


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
    invalid: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []

    for stall in layout.stalls:
        envelope_result = _access_envelope(layout.site, stall, aisle_by_id)
        if envelope_result["envelope"] is None:
            invalid.append(
                {
                    "stall_id": stall.id,
                    "served_by_aisle_id": stall.served_by_aisle_id,
                    "reason": envelope_result["reason"],
                }
            )
            continue

        envelope = envelope_result["envelope"]
        drivable_ratio = _coverage_ratio(envelope, drivable)
        usable_ratio = _coverage_ratio(envelope, usable)
        min_ratio = _minimum_coverage_ratio(layout.site)
        record = {
            "stall_id": stall.id,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "drivable_coverage_ratio": drivable_ratio,
            "usable_coverage_ratio": usable_ratio,
            "depth": _access_depth(layout.site),
        }
        envelopes.append(record)
        if drivable_ratio + 1e-9 < min_ratio:
            invalid.append({**record, "reason": "access_envelope_not_in_drivable_aisle"})
        elif usable_ratio + 1e-9 < min_ratio:
            invalid.append({**record, "reason": "access_envelope_hits_boundary_or_obstacle"})

    return {
        "valid": not invalid,
        "checked_stalls": len(layout.stalls),
        "invalid_stalls": invalid,
        "access_depth": _access_depth(layout.site),
        "minimum_coverage_ratio": _minimum_coverage_ratio(layout.site),
        "envelopes": envelopes,
    }


def _access_envelope(site: SiteSpec, stall: ParkingStall, aisle_by_id: dict[str, ShapelyPolygon]) -> dict[str, Any]:
    if not stall.served_by_aisle_id:
        return {"envelope": None, "reason": "stall_has_no_serving_aisle"}
    aisle = aisle_by_id.get(stall.served_by_aisle_id)
    if aisle is None:
        return {"envelope": None, "reason": "serving_aisle_missing"}

    stall_polygon = ShapelyPolygon(stall.polygon)
    edge = _front_edge(stall_polygon, aisle, site.stall.width)
    if edge is None:
        return {"envelope": None, "reason": "front_access_edge_not_found"}

    envelope = _edge_access_rectangle(edge, stall_polygon, aisle, _access_depth(site))
    if envelope is None or envelope.area <= 1e-9:
        return {"envelope": None, "reason": "access_envelope_not_possible"}
    return {"envelope": envelope, "reason": "ok"}


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


def _edge_access_rectangle(edge: LineString, stall: ShapelyPolygon, aisle: ShapelyPolygon, depth: float) -> ShapelyPolygon | None:
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
    p3 = (p2[0] + normal[0] * depth, p2[1] + normal[1] * depth)
    p4 = (p1[0] + normal[0] * depth, p1[1] + normal[1] * depth)
    return ShapelyPolygon([p1, p2, p3, p4])


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


def _minimum_coverage_ratio(site: SiteSpec) -> float:
    raw = site.optimization.get("maneuver_access_coverage_ratio", 0.95)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.95
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
