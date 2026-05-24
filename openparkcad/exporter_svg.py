from __future__ import annotations

from pathlib import Path
from math import cos, radians, sin

from openparkcad.diagnostic_geometry import all_shape_points, pedestrian_emergency_shapes, site_feature_shapes
from openparkcad.geometry import bounds
from openparkcad.models import LayoutResult, Point, Polygon


def write_svg(layout: LayoutResult, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    diagnostic_shapes = site_feature_shapes(layout.site.site_features)
    diagnostic_shapes.extend(pedestrian_emergency_shapes(layout.site.pedestrian_and_emergency))

    all_points = list(layout.site.boundary)
    for obstacle in layout.site.obstacles:
        all_points.extend(obstacle)
    for entrance in layout.site.entrances:
        all_points.extend(_entrance_segment(entrance.center, entrance.width, entrance.heading_degrees))
    all_points.extend(all_shape_points(diagnostic_shapes))
    for aisle in layout.aisles:
        all_points.extend(aisle.polygon)
    for stall in layout.stalls:
        all_points.extend(stall.polygon)

    min_x, min_y, max_x, max_y = bounds(all_points)
    padding = 3.0
    width = max_x - min_x + padding * 2
    height = max_y - min_y + padding * 2

    def tx(point: Point) -> Point:
        x, y = point
        return (x - min_x + padding, max_y - y + padding)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.3f} {height:.3f}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        _polygon_svg([tx(point) for point in layout.site.boundary], "#ffffff", "#0f172a", 0.18),
    ]
    for obstacle in layout.site.obstacles:
        parts.append(_polygon_svg([tx(point) for point in obstacle], "#fecaca", "#991b1b", 0.12))
    for entrance in layout.site.entrances:
        start, end = [tx(point) for point in _entrance_segment(entrance.center, entrance.width, entrance.heading_degrees)]
        cx, cy = tx(entrance.center)
        parts.append(
            f'<line x1="{start[0]:.3f}" y1="{start[1]:.3f}" x2="{end[0]:.3f}" y2="{end[1]:.3f}" '
            'stroke="#16a34a" stroke-width="0.4" stroke-linecap="round"/>'
        )
        parts.append(f'<text x="{cx:.3f}" y="{cy - 1.0:.3f}" font-size="1.2" text-anchor="middle" fill="#166534">{entrance.id}</text>')
    for shape in diagnostic_shapes:
        style = _diagnostic_style(shape.layer)
        for poly in shape.polygons:
            parts.append(_polygon_svg([tx(point) for point in poly], style["fill"], style["stroke"], style["stroke_width"], opacity=style["opacity"]))
        for line in shape.polylines:
            parts.append(_polyline_svg([tx(point) for point in line], style["stroke"], style["stroke_width"]))
        if shape.label_point:
            cx, cy = tx(shape.label_point)
            parts.append(f'<text x="{cx:.3f}" y="{cy - 0.6:.3f}" font-size="1.0" text-anchor="middle" fill="{style["stroke"]}">{shape.id}</text>')
    for aisle in layout.aisles:
        parts.append(_polygon_svg([tx(point) for point in aisle.polygon], "#e5e7eb", "#6b7280", 0.04))
    for stall in layout.stalls:
        parts.append(_polygon_svg([tx(point) for point in stall.polygon], "#dbeafe", "#1d4ed8", 0.06))
        cx, cy = _centroid([tx(point) for point in stall.polygon])
        parts.append(f'<text x="{cx:.3f}" y="{cy:.3f}" font-size="0.9" text-anchor="middle" fill="#1e3a8a">{stall.id}</text>')
    parts.append("</svg>")

    target.write_text("\n".join(parts), encoding="utf-8")


def _polygon_svg(poly: Polygon, fill: str, stroke: str, stroke_width: float, opacity: float = 1.0) -> str:
    points = " ".join(f"{x:.3f},{y:.3f}" for x, y in poly)
    return f'<polygon points="{points}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}" opacity="{opacity:.2f}"/>'


def _polyline_svg(points: list[Point], stroke: str, stroke_width: float) -> str:
    point_text = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return f'<polyline points="{point_text}" fill="none" stroke="{stroke}" stroke-width="{stroke_width}" stroke-dasharray="0.6 0.4"/>'


def _centroid(poly: Polygon) -> Point:
    return (sum(point[0] for point in poly) / len(poly), sum(point[1] for point in poly) / len(poly))


def _entrance_segment(center: Point, width: float, heading_degrees: float) -> tuple[Point, Point]:
    normal = radians(heading_degrees + 90)
    dx = cos(normal) * width / 2
    dy = sin(normal) * width / 2
    return ((center[0] - dx, center[1] - dy), (center[0] + dx, center[1] + dy))


def _diagnostic_style(layer: str) -> dict[str, float | str]:
    if layer == "SITE_FEATURES":
        return {"fill": "#fde68a", "stroke": "#92400e", "stroke_width": 0.08, "opacity": 0.78}
    if layer == "FIRE_LANES":
        return {"fill": "#fee2e2", "stroke": "#dc2626", "stroke_width": 0.12, "opacity": 0.55}
    if layer == "PEDESTRIAN":
        return {"fill": "#dcfce7", "stroke": "#15803d", "stroke_width": 0.10, "opacity": 0.65}
    return {"fill": "#fef3c7", "stroke": "#92400e", "stroke_width": 0.08, "opacity": 0.70}
