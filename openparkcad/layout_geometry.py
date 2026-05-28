from __future__ import annotations

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon, box
from shapely.ops import unary_union

from openparkcad.models import EntranceSpec, Polygon, SiteSpec


def available_area(site: SiteSpec) -> ShapelyPolygon:
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


def main_aisle_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return unary_union(
        [
            main_aisle_polygon(site, entrance, heading_degrees, start, length),
            turnaround_polygon(site, entrance, heading_degrees, length),
        ]
    )


def branch_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    return unary_union(
        [
            branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, length),
            branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, length),
        ]
    )


def main_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return local_box_to_world(start, -site.aisle_width / 2, length, site.aisle_width / 2, entrance, heading_degrees)


def turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, length: float):
    return local_box_to_world(
        length - site.aisle_width,
        -site.aisle_width,
        length,
        site.aisle_width,
        entrance,
        heading_degrees,
    )


def branch_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width / 2, -site.aisle_width / 2, branch_u + site.aisle_width / 2, length)
    else:
        local = (branch_u - site.aisle_width / 2, -length, branch_u + site.aisle_width / 2, site.aisle_width / 2)
    return local_box_to_world(*local, entrance, heading_degrees)


def branch_turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width, length - site.aisle_width, branch_u + site.aisle_width, length)
    else:
        local = (branch_u - site.aisle_width, -length, branch_u + site.aisle_width, -length + site.aisle_width)
    return local_box_to_world(*local, entrance, heading_degrees)


def local_box_to_world(u1: float, v1: float, u2: float, v2: float, entrance: EntranceSpec, heading_degrees: float):
    geometry = box(u1, v1, u2, v2)
    rotated = affinity.rotate(geometry, heading_degrees, origin=(0, 0), use_radians=False)
    return affinity.translate(rotated, xoff=entrance.center[0], yoff=entrance.center[1])


def normalized_local_box_to_world(local: tuple[float, float, float, float], entrance: EntranceSpec, heading_degrees: float):
    u1, v1, u2, v2 = local
    return local_box_to_world(min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2), entrance, heading_degrees)


def area_overlaps(a, b) -> bool:
    return a.intersection(b).area > 1e-6


def polygon_points(poly) -> Polygon:
    polygons = _iter_polygons(poly)
    if polygons:
        largest = max(polygons, key=lambda item: item.area)
        coords = list(largest.exterior.coords[:-1])
    else:
        coords = list(poly.exterior.coords[:-1])
    return [(float(x), float(y)) for x, y in coords]


def _iter_polygons(geometry) -> list[ShapelyPolygon]:
    if isinstance(geometry, ShapelyPolygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        return [item for item in geometry.geoms if isinstance(item, ShapelyPolygon)]
    return []
