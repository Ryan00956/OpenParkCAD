from __future__ import annotations

from dataclasses import replace
import math

from shapely import affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon as ShapelyPolygon, box
from shapely.ops import unary_union

from openparkcad.models import (
    AisleClassSpec,
    AngleAttempt,
    EntranceSpec,
    LayoutResult,
    ParkingAisle,
    ParkingStall,
    Polygon,
    SiteSpec,
)
from openparkcad.scoring import score_layout, score_total


def generate_layout(site: SiteSpec) -> LayoutResult:
    """Generate the current Phase 1 layout.

    Phase 1 deliberately supports one conservative pattern:

    entrance -> straight wide two-way main aisle -> end turnaround -> stalls on both sides
    """
    attempts: list[AngleAttempt] = []
    best: LayoutResult | None = None
    unsupported = phase1_unsupported_inputs(site)

    for entrance in _entry_capable_entrances(site):
        for heading_delta in _heading_deltas(site):
            heading = entrance.heading_degrees + heading_delta
            for offset in _entrance_offsets(site, entrance):
                offset_entrance = _offset_entrance(entrance, offset)
                layout = _generate_for_entrance(site, offset_entrance, heading, heading_delta, offset)
                attempts.append(
                    AngleAttempt(
                        angle_degrees=heading,
                        stall_count=layout.stall_count,
                        entrance_id=entrance.id,
                        heading_delta_degrees=heading_delta,
                        entrance_offset=offset,
                        branch_side=layout.selected_branch_side,
                        branch_start_u=layout.selected_branch_start_u,
                        branch_length=layout.selected_branch_length,
                        branch_candidates=list(getattr(layout, "_branch_candidates", [])),
                    )
                )
                if best is None or score_total(layout) > score_total(best):
                    best = layout

    if best is None:
        return LayoutResult(site=site, stalls=[], generation_mode="phase1_main_aisle", unsupported_phase1_inputs=unsupported)

    result = LayoutResult(
        site=site,
        stalls=best.stalls,
        aisles=best.aisles,
        selected_angle_degrees=best.selected_angle_degrees,
        attempts=attempts,
        generation_mode="phase1_main_aisle",
        main_entrance_id=best.main_entrance_id,
        selected_heading_degrees=best.selected_heading_degrees,
        selected_heading_delta_degrees=best.selected_heading_delta_degrees,
        selected_entrance_offset=best.selected_entrance_offset,
        selected_branch_side=best.selected_branch_side,
        selected_branch_start_u=best.selected_branch_start_u,
        selected_branch_length=best.selected_branch_length,
        unsupported_phase1_inputs=unsupported,
    )
    return _with_score(result)


def _generate_for_entrance(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
) -> LayoutResult:
    if not _supports_phase1_aisle(site) or not _supports_phase1_stall(site) or entrance.width + 1e-9 < site.aisle_width:
        return _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset)

    available = _available_area(site)
    start = max(site.margin, 0.0)
    aisle_length = _max_clear_aisle_length(site, available, entrance, heading_degrees, start)
    min_length = start + site.aisle_width * 2
    if aisle_length <= min_length:
        return _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset)

    main_aisle = _main_aisle_polygon(site, entrance, heading_degrees, start, aisle_length)
    turnaround = _turnaround_polygon(site, entrance, heading_degrees, aisle_length)
    aisles = [
        ParkingAisle(
            id="A-MAIN",
            polygon=_polygon_points(main_aisle),
            angle_degrees=heading_degrees,
            role="main",
            connected_to_entrance_id=entrance.id,
        ),
        ParkingAisle(
            id="A-TURNAROUND",
            polygon=_polygon_points(turnaround),
            angle_degrees=heading_degrees,
            role="turnaround",
            parent_aisle_id="A-MAIN",
        ),
    ]
    stalls = _stalls_along_main_aisle(
        site,
        available,
        entrance,
        heading_degrees,
        start_u=site.aisle_width,
        end_u=aisle_length - site.aisle_width,
    )

    base = _with_score(LayoutResult(
        site=site,
        stalls=stalls,
        aisles=aisles,
        selected_angle_degrees=heading_degrees,
        generation_mode="phase1_main_aisle",
        main_entrance_id=entrance.id,
        selected_heading_degrees=heading_degrees,
        selected_heading_delta_degrees=heading_delta_degrees,
        selected_entrance_offset=entrance_offset,
        unsupported_phase1_inputs=phase1_unsupported_inputs(site),
    ))
    return _with_best_branch(site, available, entrance, heading_degrees, base, aisle_length)


def _empty_layout(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
) -> LayoutResult:
    return _with_score(LayoutResult(
        site=site,
        stalls=[],
        selected_angle_degrees=heading_degrees,
        generation_mode="phase1_main_aisle",
        main_entrance_id=entrance.id,
        selected_heading_degrees=heading_degrees,
        selected_heading_delta_degrees=heading_delta_degrees,
        selected_entrance_offset=entrance_offset,
        unsupported_phase1_inputs=phase1_unsupported_inputs(site),
    ))


def _stalls_along_main_aisle(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start_u: float, end_u: float) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    u = start_u
    while u + site.stall.width <= end_u:
        if not _module_angle_allowed(90.0, site.stall.allowed_angles):
            break
        for side in ("left", "right"):
            if side == "left":
                local = (u, site.aisle_width / 2, u + site.stall.width, site.aisle_width / 2 + site.stall.length)
            else:
                local = (u, -site.aisle_width / 2 - site.stall.length, u + site.stall.width, -site.aisle_width / 2)
            stall = _local_box_to_world(*local, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=_polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id="A-MAIN",
                        aisle_side=side,
                    )
                )
        u += site.stall.width
    return stalls


def _with_best_branch(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    main_aisle_length: float,
) -> LayoutResult:
    if not site.optimization.get("enable_branches", True):
        return base

    best = base
    branch_candidates: list[dict[str, object]] = []
    for branch_u in _branch_start_positions(site, main_aisle_length):
        for side in ("left", "right"):
            candidate, diagnostic = _branch_layout(site, available, entrance, heading_degrees, base, main_aisle_length, branch_u, side)
            branch_candidates.append(diagnostic)
            if candidate and score_total(candidate) > score_total(best):
                best = candidate
    object.__setattr__(best, "_branch_candidates", branch_candidates)
    return best


def _branch_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    main_aisle_length: float,
    branch_u: float,
    side: str,
) -> tuple[LayoutResult | None, dict[str, object]]:
    branch_length = _max_clear_branch_length(site, available, entrance, heading_degrees, branch_u, side)
    diagnostic: dict[str, object] = {
        "side": side,
        "start_u": branch_u,
        "length": branch_length,
        "base_stall_count": base.stall_count,
        "stall_count": None,
        "reason": "candidate_evaluated",
    }
    if branch_length <= site.aisle_width * 2:
        diagnostic["reason"] = "branch_too_short_for_turnaround"
        return None, diagnostic

    branch_aisle = _branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_turnaround = _branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_drivable = unary_union([branch_aisle, branch_turnaround])
    if not available.covers(branch_drivable):
        diagnostic["reason"] = "branch_geometry_outside_usable_area"
        return None, diagnostic

    kept_main_stalls = [
        stall for stall in base.stalls if not _area_overlaps(ShapelyPolygon(stall.polygon), branch_drivable)
    ]
    occupied = unary_union(
        [ShapelyPolygon(aisle.polygon) for aisle in base.aisles]
        + [branch_drivable]
        + [ShapelyPolygon(stall.polygon) for stall in kept_main_stalls]
    )
    branch_stalls = _stalls_along_branch(
        site,
        available,
        entrance,
        heading_degrees,
        branch_u,
        side,
        start_t=site.aisle_width,
        end_t=branch_length - site.aisle_width,
        occupied=occupied,
        start_index=len(kept_main_stalls) + 1,
    )
    stalls = _renumber_stalls(kept_main_stalls + branch_stalls)
    aisles = list(base.aisles) + [
        ParkingAisle(
            id="A-BRANCH-001",
            polygon=_polygon_points(branch_aisle),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="branch",
            parent_aisle_id="A-MAIN",
        ),
        ParkingAisle(
            id="A-BRANCH-001-TURNAROUND",
            polygon=_polygon_points(branch_turnaround),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="turnaround",
            parent_aisle_id="A-BRANCH-001",
        ),
    ]
    result = _with_score(LayoutResult(
        site=site,
        stalls=stalls,
        aisles=aisles,
        selected_angle_degrees=base.selected_angle_degrees,
        generation_mode=base.generation_mode,
        main_entrance_id=base.main_entrance_id,
        selected_heading_degrees=base.selected_heading_degrees,
        selected_heading_delta_degrees=base.selected_heading_delta_degrees,
        selected_entrance_offset=base.selected_entrance_offset,
        selected_branch_side=side,
        selected_branch_start_u=branch_u,
        selected_branch_length=branch_length,
        unsupported_phase1_inputs=base.unsupported_phase1_inputs,
    ))
    diagnostic["stall_count"] = result.stall_count
    if result.stall_count <= base.stall_count:
        diagnostic["reason"] = "branch_does_not_improve_stall_count"
        return result, diagnostic
    diagnostic["reason"] = "branch_improves_stall_count"
    return result, diagnostic


def _stalls_along_branch(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    branch_u: float,
    side: str,
    start_t: float,
    end_t: float,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    t = start_t
    direction = 1 if side == "left" else -1
    while t + site.stall.width <= end_t:
        if not _module_angle_allowed(90.0, site.stall.allowed_angles):
            break
        for stall_side in ("left", "right"):
            if stall_side == "left":
                local = (
                    branch_u + site.aisle_width / 2,
                    direction * t,
                    branch_u + site.aisle_width / 2 + site.stall.length,
                    direction * (t + site.stall.width),
                )
            else:
                local = (
                    branch_u - site.aisle_width / 2 - site.stall.length,
                    direction * t,
                    branch_u - site.aisle_width / 2,
                    direction * (t + site.stall.width),
                )
            stall = _normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not _area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=_polygon_points(stall),
                        angle_degrees=heading_degrees + (90 if side == "left" else -90),
                        served_by_aisle_id="A-BRANCH-001",
                        aisle_side=stall_side,
                    )
                )
        t += site.stall.width
    return stalls


def _max_clear_aisle_length(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start: float) -> float:
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(site.stall.width, 0.5)
    best = start
    length = start + site.aisle_width * 2

    while length <= diagonal * 1.5:
        candidate = _main_aisle_with_turnaround(site, entrance, heading_degrees, start, length)
        if not available.covers(candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal * 1.5)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = _main_aisle_with_turnaround(site, entrance, heading_degrees, start, mid)
        if available.covers(candidate):
            low = mid
        else:
            high = mid
    return low


def _max_clear_branch_length(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str) -> float:
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(site.stall.width, 0.5)
    best = 0.0
    length = site.aisle_width * 2

    while length <= diagonal:
        candidate = _branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, length)
        if not available.covers(candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = _branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, mid)
        if available.covers(candidate):
            low = mid
        else:
            high = mid
    return low


def _main_aisle_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return unary_union(
        [
            _main_aisle_polygon(site, entrance, heading_degrees, start, length),
            _turnaround_polygon(site, entrance, heading_degrees, length),
        ]
    )


def _branch_with_turnaround(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    return unary_union(
        [
            _branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, length),
            _branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, length),
        ]
    )


def _main_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, start: float, length: float):
    return _local_box_to_world(start, -site.aisle_width / 2, length, site.aisle_width / 2, entrance, heading_degrees)


def _turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, length: float):
    return _local_box_to_world(
        length - site.aisle_width,
        -site.aisle_width,
        length,
        site.aisle_width,
        entrance,
        heading_degrees,
    )


def _branch_aisle_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width / 2, -site.aisle_width / 2, branch_u + site.aisle_width / 2, length)
    else:
        local = (branch_u - site.aisle_width / 2, -length, branch_u + site.aisle_width / 2, site.aisle_width / 2)
    return _local_box_to_world(*local, entrance, heading_degrees)


def _branch_turnaround_polygon(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_u: float, side: str, length: float):
    if side == "left":
        local = (branch_u - site.aisle_width, length - site.aisle_width, branch_u + site.aisle_width, length)
    else:
        local = (branch_u - site.aisle_width, -length, branch_u + site.aisle_width, -length + site.aisle_width)
    return _local_box_to_world(*local, entrance, heading_degrees)


def _local_box_to_world(u1: float, v1: float, u2: float, v2: float, entrance: EntranceSpec, heading_degrees: float):
    geometry = box(u1, v1, u2, v2)
    rotated = affinity.rotate(geometry, heading_degrees, origin=(0, 0), use_radians=False)
    return affinity.translate(rotated, xoff=entrance.center[0], yoff=entrance.center[1])


def _normalized_local_box_to_world(local: tuple[float, float, float, float], entrance: EntranceSpec, heading_degrees: float):
    u1, v1, u2, v2 = local
    return _local_box_to_world(min(u1, u2), min(v1, v2), max(u1, u2), max(v1, v2), entrance, heading_degrees)


def _branch_start_positions(site: SiteSpec, main_aisle_length: float) -> tuple[float, ...]:
    raw = site.optimization.get("branch_start_positions")
    min_u = site.aisle_width * 2
    max_u = main_aisle_length - site.aisle_width * 2
    if max_u <= min_u:
        return ()
    if isinstance(raw, list):
        return tuple(sorted({float(item) for item in raw if min_u <= float(item) <= max_u}))
    step = float(site.optimization.get("branch_start_step", site.stall.width * 2))
    step = max(step, site.stall.width)
    positions: list[float] = []
    u = min_u
    while u <= max_u + 1e-9:
        positions.append(round(u, 6))
        u += step
    midpoint = round((min_u + max_u) / 2, 6)
    positions.append(midpoint)
    positions.append(round(max_u, 6))
    return tuple(sorted(set(positions)))


def _heading_deltas(site: SiteSpec) -> tuple[float, ...]:
    raw = site.optimization.get("heading_deltas_degrees")
    if isinstance(raw, list):
        return tuple(float(item) for item in raw)
    return (-20.0, -10.0, 0.0, 10.0, 20.0)


def _entrance_offsets(site: SiteSpec, entrance: EntranceSpec) -> tuple[float, ...]:
    raw = site.optimization.get("entrance_offsets")
    max_offset = max((entrance.width - site.aisle_width) / 2, 0.0)
    if isinstance(raw, list):
        offsets = tuple(float(item) for item in raw)
    elif max_offset > 0:
        offsets = (-max_offset, 0.0, max_offset)
    else:
        offsets = (0.0,)
    return tuple(offset for offset in offsets if abs(offset) <= max_offset + 1e-9)


def _offset_entrance(entrance: EntranceSpec, offset: float) -> EntranceSpec:
    normal = math.radians(entrance.heading_degrees + 90)
    center = (
        entrance.center[0] + math.cos(normal) * offset,
        entrance.center[1] + math.sin(normal) * offset,
    )
    return replace(entrance, center=center)


def _renumber_stalls(stalls: list[ParkingStall]) -> list[ParkingStall]:
    return [
        ParkingStall(
            id=f"P-{index:03d}",
            polygon=stall.polygon,
            angle_degrees=stall.angle_degrees,
            served_by_aisle_id=stall.served_by_aisle_id,
            aisle_side=stall.aisle_side,
        )
        for index, stall in enumerate(stalls, start=1)
    ]


def _area_overlaps(a, b) -> bool:
    return a.intersection(b).area > 1e-6


def _with_score(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "score", score_layout(layout))
    return layout


def _entry_capable_entrances(site: SiteSpec) -> list[EntranceSpec]:
    return [
        entrance
        for entrance in site.entrances
        if entrance.mode in {"shared", "entry_only"} or "enter" in entrance.allowed_movements
    ]


def _supports_phase1_aisle(site: SiteSpec) -> bool:
    if site.aisle_selection_mode != "fixed":
        return False
    fixed_class = _fixed_aisle_class(site)
    if fixed_class is None:
        return False
    return fixed_class.enabled and fixed_class.capacity == "two_vehicle" and fixed_class.directionality == "two_way"


def _supports_phase1_stall(site: SiteSpec) -> bool:
    stall = site.stall
    return (
        stall.family == "perpendicular"
        and _module_angle_allowed(90.0, stall.allowed_angles)
        and not stall.drive_over
        and "front" in stall.access_sides
        and "front" not in stall.blocked_sides
    )


def phase1_unsupported_inputs(site: SiteSpec) -> list[dict[str, str]]:
    """Return Phase 1 support notes for accepted-but-not-generated inputs."""
    issues: list[dict[str, str]] = []
    fixed_class = _fixed_aisle_class(site)
    if site.aisle_selection_mode != "fixed":
        issues.append(
            {
                "field": "aisles.selection_mode",
                "value": site.aisle_selection_mode,
                "reason": "Phase 1 only generates a fixed wide two-way aisle class.",
            }
        )
    if fixed_class is None:
        issues.append(
            {
                "field": "aisles.fixed_class",
                "value": str(site.fixed_aisle_class),
                "reason": "Phase 1 needs a resolvable fixed aisle class.",
            }
        )
    elif not _is_phase1_aisle_class(fixed_class):
        issues.append(
            {
                "field": f"aisles.classes.{fixed_class.id}",
                "value": f"capacity={fixed_class.capacity}, directionality={fixed_class.directionality}, enabled={fixed_class.enabled}",
                "reason": "Phase 1 only supports enabled two_vehicle/two_way aisle classes.",
            }
        )

    stall = site.stall
    if stall.family != "perpendicular":
        issues.append(
            {
                "field": "parking.active_stall.family",
                "value": stall.family,
                "reason": "Phase 1 only generates standard perpendicular stalls.",
            }
        )
    if not _module_angle_allowed(90.0, stall.allowed_angles):
        issues.append(
            {
                "field": "parking.active_stall.allowed_angles",
                "value": ",".join(str(angle) for angle in stall.allowed_angles),
                "reason": "Phase 1 only places 90-degree stalls.",
            }
        )
    if stall.drive_over:
        issues.append(
            {
                "field": "parking.active_stall.drive_over",
                "value": "true",
                "reason": "Drive-over painted stalls require later maneuver/traffic logic.",
            }
        )
    if "front" not in stall.access_sides:
        issues.append(
            {
                "field": "parking.active_stall.access_sides",
                "value": ",".join(stall.access_sides),
                "reason": "Phase 1 only models aisle-facing front access.",
            }
        )
    if "front" in stall.blocked_sides:
        issues.append(
            {
                "field": "parking.active_stall.blocked_sides",
                "value": ",".join(stall.blocked_sides),
                "reason": "The aisle-facing side cannot be blocked in the Phase 1 maneuver approximation.",
            }
        )
    return issues


def _is_phase1_aisle_class(aisle_class: AisleClassSpec) -> bool:
    return aisle_class.enabled and aisle_class.capacity == "two_vehicle" and aisle_class.directionality == "two_way"


def _module_angle_allowed(angle: float, allowed_angles: tuple[float, ...]) -> bool:
    normalized = angle % 180
    return any(abs(normalized - (allowed % 180)) <= 1e-6 for allowed in allowed_angles)


def _fixed_aisle_class(site: SiteSpec):
    if not site.fixed_aisle_class:
        return site.aisle_classes[0] if site.aisle_classes else None
    for aisle_class in site.aisle_classes:
        if aisle_class.id == site.fixed_aisle_class:
            return aisle_class
    return None


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


def _iter_polygons(geometry) -> list[ShapelyPolygon]:
    if isinstance(geometry, ShapelyPolygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon | GeometryCollection):
        return [item for item in geometry.geoms if isinstance(item, ShapelyPolygon)]
    return []


def _polygon_points(poly) -> Polygon:
    polygons = _iter_polygons(poly)
    if polygons:
        largest = max(polygons, key=lambda item: item.area)
        coords = list(largest.exterior.coords[:-1])
    else:
        coords = list(poly.exterior.coords[:-1])
    return [(float(x), float(y)) for x, y in coords]
