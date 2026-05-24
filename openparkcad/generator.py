from __future__ import annotations

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon, box
from shapely.ops import unary_union

from openparkcad.models import (
    AngleAttempt,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    Point,
    Polygon,
    SiteSpec,
)


def generate_layout(site: SiteSpec) -> LayoutResult:
    attempts: list[AngleAttempt] = []
    best: LayoutResult | None = None

    angles = site.candidate_angles or (site.angle_degrees,)
    for angle in angles:
        layout = _generate_for_angle(site, angle)
        attempts.append(AngleAttempt(angle_degrees=angle, stall_count=layout.stall_count))
        if best is None or layout.stall_count > best.stall_count:
            best = layout

    if best is None:
        return LayoutResult(site=site, stalls=[], selected_angle_degrees=site.angle_degrees)

    return LayoutResult(
        site=site,
        stalls=best.stalls,
        aisles=best.aisles,
        selected_angle_degrees=best.selected_angle_degrees,
        attempts=attempts,
    )


def _generate_for_angle(site: SiteSpec, angle: float) -> LayoutResult:
    available = _available_area(site)
    local_available = affinity.rotate(available, -angle, origin=(0, 0), use_radians=False)
    min_x, min_y, max_x, max_y = local_available.bounds

    width = site.stall.width
    length = site.stall.length
    row_pitch = length + site.aisle_width

    stalls: list[ParkingStall] = []
    access_pads = []

    y = _snap_down(min_y, row_pitch)
    while y + length + site.aisle_width <= max_y:
        x = _snap_down(min_x, width)
        while x + width <= max_x:
            local_stall = box(x, y, x + width, y + length)
            local_access = box(x, y + length, x + width, y + length + site.aisle_width)
            stall = affinity.rotate(local_stall, angle, origin=(0, 0), use_radians=False)
            access = affinity.rotate(local_access, angle, origin=(0, 0), use_radians=False)

            # A counted stall must fit and must have a clear aisle strip in front.
            if available.covers(stall) and available.covers(access):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(ParkingStall(id=stall_id, polygon=_polygon_points(stall), angle_degrees=angle))
                access_pads.append(access)
            x += width
        y += row_pitch

    aisles = _aisles_from_access_pads(access_pads, angle)
    return LayoutResult(site=site, stalls=stalls, aisles=aisles, selected_angle_degrees=angle)


def _available_area(site: SiteSpec) -> ShapelyPolygon:
    boundary = ShapelyPolygon(site.boundary)
    if not boundary.is_valid:
        boundary = boundary.buffer(0)

    usable = boundary.buffer(-site.margin, join_style="mitre")
    if usable.is_empty:
        usable = boundary

    obstacles = [ShapelyPolygon(item) for item in site.obstacles]
    if not obstacles:
        return usable
    return usable.difference(unary_union(obstacles))


def _aisles_from_access_pads(access_pads, angle: float) -> list[ParkingAisle]:
    if not access_pads:
        return []

    merged = unary_union(access_pads)
    polygons = _iter_polygons(merged)
    aisles: list[ParkingAisle] = []
    for index, poly in enumerate(polygons, start=1):
        if poly.area <= 1e-6:
            continue
        aisles.append(ParkingAisle(id=f"A-{index:03d}", polygon=_polygon_points(poly), angle_degrees=angle))
    return aisles


def _iter_polygons(geometry) -> list[ShapelyPolygon]:
    if isinstance(geometry, ShapelyPolygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        return [item for item in geometry.geoms if isinstance(item, ShapelyPolygon)]
    return []


def _polygon_points(poly: ShapelyPolygon) -> Polygon:
    coords = list(poly.exterior.coords[:-1])
    return [(float(x), float(y)) for x, y in coords]


def _snap_down(value: float, step: float) -> float:
    return int(value / step) * step
