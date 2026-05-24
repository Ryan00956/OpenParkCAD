from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, pi, radians, sin
from typing import Any

from shapely.geometry import LineString

from openparkcad.models import Point, Polygon


@dataclass(frozen=True)
class DiagnosticShape:
    id: str
    layer: str
    polygons: list[Polygon] = field(default_factory=list)
    polylines: list[list[Point]] = field(default_factory=list)
    label_point: Point | None = None


def site_feature_shapes(features: list[dict[str, Any]]) -> list[DiagnosticShape]:
    shapes: list[DiagnosticShape] = []
    for index, feature in enumerate(features, start=1):
        feature_id = str(feature.get("id", f"site-feature-{index}"))
        geometry = feature.get("geometry")
        shape = _shape_from_geometry(feature_id, "SITE_FEATURES", geometry)
        if shape:
            shapes.append(shape)
    return shapes


def pedestrian_emergency_shapes(data: dict[str, Any]) -> list[DiagnosticShape]:
    shapes: list[DiagnosticShape] = []
    for key, layer in (("pedestrian_routes", "PEDESTRIAN"), ("accessible_routes", "PEDESTRIAN"), ("fire_lanes", "FIRE_LANES")):
        for index, item in enumerate(data.get(key, []), start=1):
            item_id = str(item.get("id", f"{key}-{index}"))
            geometry = item.get("geometry")
            shape = _shape_from_geometry(item_id, layer, geometry)
            if shape:
                shapes.append(shape)
    return shapes


def _shape_from_geometry(shape_id: str, layer: str, geometry: Any) -> DiagnosticShape | None:
    if not isinstance(geometry, dict):
        return None

    geometry_type = geometry.get("type")
    if geometry_type == "polygon":
        points = [_point(item) for item in geometry.get("points", [])]
        return DiagnosticShape(id=shape_id, layer=layer, polygons=[points], label_point=_centroid(points))
    if geometry_type == "circle":
        center = _point(geometry["center"])
        radius = float(geometry["radius"])
        points = _circle_points(center, radius)
        return DiagnosticShape(id=shape_id, layer=layer, polygons=[points], label_point=center)
    if geometry_type == "rectangle":
        points = _rectangle_points(
            origin=_point(geometry["origin"]),
            width=float(geometry["width"]),
            height=float(geometry["height"]),
            rotation_degrees=float(geometry.get("rotation_degrees", 0.0)),
        )
        return DiagnosticShape(id=shape_id, layer=layer, polygons=[points], label_point=_centroid(points))
    if geometry_type == "polyline_buffer":
        points = [_point(item) for item in geometry.get("points", [])]
        width = float(geometry.get("width", 0.0))
        if len(points) < 2 or width <= 0:
            return DiagnosticShape(id=shape_id, layer=layer, polylines=[points], label_point=points[0] if points else None)
        buffered = LineString(points).buffer(width / 2, cap_style="flat", join_style="mitre")
        poly = [(float(x), float(y)) for x, y in list(buffered.exterior.coords[:-1])]
        return DiagnosticShape(id=shape_id, layer=layer, polygons=[poly], polylines=[points], label_point=_centroid(points))
    return None


def all_shape_points(shapes: list[DiagnosticShape]) -> list[Point]:
    points: list[Point] = []
    for shape in shapes:
        for poly in shape.polygons:
            points.extend(poly)
        for line in shape.polylines:
            points.extend(line)
        if shape.label_point:
            points.append(shape.label_point)
    return points


def _point(raw: Any) -> Point:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"Point must be [x, y], got {raw!r}")
    return (float(raw[0]), float(raw[1]))


def _circle_points(center: Point, radius: float, segments: int = 32) -> Polygon:
    return [
        (
            center[0] + cos(2 * pi * index / segments) * radius,
            center[1] + sin(2 * pi * index / segments) * radius,
        )
        for index in range(segments)
    ]


def _rectangle_points(origin: Point, width: float, height: float, rotation_degrees: float) -> Polygon:
    ox, oy = origin
    local = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    angle = radians(rotation_degrees)
    cos_a = cos(angle)
    sin_a = sin(angle)
    return [(ox + x * cos_a - y * sin_a, oy + x * sin_a + y * cos_a) for x, y in local]


def _centroid(points: list[Point]) -> Point:
    return (sum(point[0] for point in points) / len(points), sum(point[1] for point in points) / len(points))
