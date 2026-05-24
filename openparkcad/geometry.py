from __future__ import annotations

import math

from openparkcad.models import Point, Polygon

EPSILON = 1e-9


def rotate_point(point: Point, angle_degrees: float) -> Point:
    radians = math.radians(angle_degrees)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)
    x, y = point
    return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)


def bounds(points: list[Point]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def polygon_edges(poly: Polygon) -> list[tuple[Point, Point]]:
    return list(zip(poly, poly[1:] + poly[:1]))


def point_on_segment(point: Point, start: Point, end: Point) -> bool:
    px, py = point
    ax, ay = start
    bx, by = end
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > EPSILON:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -EPSILON:
        return False
    length_sq = (bx - ax) ** 2 + (by - ay) ** 2
    return dot <= length_sq + EPSILON


def point_in_polygon(point: Point, poly: Polygon, include_boundary: bool = True) -> bool:
    for start, end in polygon_edges(poly):
        if point_on_segment(point, start, end):
            return include_boundary

    x, y = point
    inside = False
    for start, end in polygon_edges(poly):
        x1, y1 = start
        x2, y2 = end
        crosses_ray = (y1 > y) != (y2 > y)
        if crosses_ray:
            x_intersect = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_intersect:
                inside = not inside
    return inside


def orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)

    if abs(o1) <= EPSILON and point_on_segment(c, a, b):
        return True
    if abs(o2) <= EPSILON and point_on_segment(d, a, b):
        return True
    if abs(o3) <= EPSILON and point_on_segment(a, c, d):
        return True
    if abs(o4) <= EPSILON and point_on_segment(b, c, d):
        return True

    return (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0)


def polygons_intersect(a: Polygon, b: Polygon) -> bool:
    for point in a:
        if point_in_polygon(point, b):
            return True
    for point in b:
        if point_in_polygon(point, a):
            return True
    for edge_a in polygon_edges(a):
        for edge_b in polygon_edges(b):
            if segments_intersect(edge_a[0], edge_a[1], edge_b[0], edge_b[1]):
                return True
    return False


def polygon_inside_polygon(inner: Polygon, outer: Polygon) -> bool:
    points_to_check = list(inner)
    for start, end in polygon_edges(inner):
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        points_to_check.append(midpoint)
    return all(point_in_polygon(point, outer) for point in points_to_check)


def offset_rectangle(local_x: float, local_y: float, width: float, length: float, angle: float) -> Polygon:
    local = [
        (local_x, local_y),
        (local_x + width, local_y),
        (local_x + width, local_y + length),
        (local_x, local_y + length),
    ]
    return [rotate_point(point, angle) for point in local]


def shrink_polygon_toward_centroid(poly: Polygon, distance: float) -> Polygon:
    if distance <= 0:
        return poly
    cx = sum(point[0] for point in poly) / len(poly)
    cy = sum(point[1] for point in poly) / len(poly)
    result: Polygon = []
    for x, y in poly:
        dx = cx - x
        dy = cy - y
        length = math.hypot(dx, dy)
        if length <= EPSILON:
            result.append((x, y))
        else:
            move = min(distance, length * 0.45)
            result.append((x + dx / length * move, y + dy / length * move))
    return result
