from __future__ import annotations

from itertools import combinations
from typing import Any

from shapely.geometry import Point as ShapelyPoint, Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec


def operational_quality_report(layout: LayoutResult) -> dict[str, Any]:
    junctions = _junction_reports(layout)
    entrance_throats = _entrance_throat_reports(layout)
    junction_conflicts = sum(len(item["conflicting_stalls"]) for item in junctions)
    entrance_conflicts = sum(len(item["conflicting_stalls"]) for item in entrance_throats)
    risk_score = float(junction_conflicts + entrance_conflicts)
    warnings: list[str] = []
    if junction_conflicts:
        warnings.append(f"{junction_conflicts} stall-junction clearance conflicts are reported as soft risks")
    if entrance_conflicts:
        warnings.append(f"{entrance_conflicts} stall-entrance throat conflicts are reported as soft risks")
    return {
        "version": "phase5a-1",
        "status": "report_only",
        "valid": True,
        "risk_score": risk_score,
        "junction_count": len(junctions),
        "junction_conflict_count": junction_conflicts,
        "entrance_throat_count": len(entrance_throats),
        "entrance_throat_conflict_count": entrance_conflicts,
        "warnings": warnings,
        "junctions": junctions,
        "entrance_throats": entrance_throats,
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


def _nonnegative_float(raw: object, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default
    return max(value, 0.0)
