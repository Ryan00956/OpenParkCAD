from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import math

from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from openparkcad.layout_geometry import (
    area_overlaps,
    available_area,
    branch_aisle_polygon,
    branch_turnaround_polygon,
    branch_with_turnaround,
    main_aisle_polygon,
    main_aisle_with_turnaround,
    normalized_local_box_to_world,
    polygon_points,
    turnaround_polygon,
)
from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec
from openparkcad.phase1_support import (
    entry_capable_entrances,
    module_angle_allowed,
    phase1_unsupported_inputs,
    supports_phase1_aisle,
    supports_phase1_stall,
)


FinalizeLayout = Callable[[LayoutResult], LayoutResult]
GraphValid = Callable[[LayoutResult], bool]
LayoutScoreTotal = Callable[[LayoutResult], float]


@dataclass(frozen=True)
class Phase1Candidate:
    layout: LayoutResult
    entrance_id: str | None
    heading_degrees: float
    heading_delta_degrees: float
    entrance_offset: float
    branch_candidates: list[dict[str, object]]


def iter_phase1_candidates(
    site: SiteSpec,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> list[Phase1Candidate]:
    candidates: list[Phase1Candidate] = []
    for entrance in entry_capable_entrances(site):
        for heading_delta in _heading_deltas(site):
            heading = entrance.heading_degrees + heading_delta
            for offset in _entrance_offsets(site, entrance):
                offset_entrance = _offset_entrance(entrance, offset)
                layout, branch_candidates = _generate_for_entrance(
                    site,
                    offset_entrance,
                    heading,
                    heading_delta,
                    offset,
                    finalize_layout,
                    graph_valid,
                    score_total,
                )
                candidates.append(
                    Phase1Candidate(
                        layout=layout,
                        entrance_id=entrance.id,
                        heading_degrees=heading,
                        heading_delta_degrees=heading_delta,
                        entrance_offset=offset,
                        branch_candidates=branch_candidates,
                    )
                )
    return candidates


def _generate_for_entrance(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    if not supports_phase1_aisle(site) or not supports_phase1_stall(site) or entrance.width + 1e-9 < site.aisle_width:
        return _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset, finalize_layout), []

    available = available_area(site)
    start = max(site.margin, 0.0)
    aisle_length = _max_clear_aisle_length(site, available, entrance, heading_degrees, start)
    min_length = start + site.aisle_width * 2
    if aisle_length <= min_length:
        return _empty_layout(site, entrance, heading_degrees, heading_delta_degrees, entrance_offset, finalize_layout), []

    main_aisle = main_aisle_polygon(site, entrance, heading_degrees, start, aisle_length)
    turnaround = turnaround_polygon(site, entrance, heading_degrees, aisle_length)
    aisles = [
        ParkingAisle(
            id="A-MAIN",
            polygon=polygon_points(main_aisle),
            angle_degrees=heading_degrees,
            role="main",
            connected_to_entrance_id=entrance.id,
        ),
        ParkingAisle(
            id="A-TURNAROUND",
            polygon=polygon_points(turnaround),
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

    base = finalize_layout(
        LayoutResult(
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
        )
    )
    return _with_best_branch(site, available, entrance, heading_degrees, base, aisle_length, finalize_layout, graph_valid, score_total)


def _empty_layout(
    site: SiteSpec,
    entrance: EntranceSpec,
    heading_degrees: float,
    heading_delta_degrees: float,
    entrance_offset: float,
    finalize_layout: FinalizeLayout,
) -> LayoutResult:
    return finalize_layout(
        LayoutResult(
            site=site,
            stalls=[],
            selected_angle_degrees=heading_degrees,
            generation_mode="phase1_main_aisle",
            main_entrance_id=entrance.id,
            selected_heading_degrees=heading_degrees,
            selected_heading_delta_degrees=heading_delta_degrees,
            selected_entrance_offset=entrance_offset,
            unsupported_phase1_inputs=phase1_unsupported_inputs(site),
        )
    )


def _stalls_along_main_aisle(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start_u: float, end_u: float) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    u = start_u
    while u + site.stall.width <= end_u:
        if not module_angle_allowed(90.0, site.stall.allowed_angles):
            break
        for side in ("left", "right"):
            if side == "left":
                local = (u, site.aisle_width / 2, u + site.stall.width, site.aisle_width / 2 + site.stall.length)
            else:
                local = (u, -site.aisle_width / 2 - site.stall.length, u + site.stall.width, -site.aisle_width / 2)
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
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
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult, list[dict[str, object]]]:
    if not site.optimization.get("enable_branches", True):
        return base, []

    best = base
    branch_candidates: list[dict[str, object]] = []
    for branch_u in _branch_start_positions(site, main_aisle_length):
        for side in ("left", "right"):
            candidate, diagnostic = _branch_layout(site, available, entrance, heading_degrees, base, branch_u, side, finalize_layout, graph_valid)
            branch_candidates.append(diagnostic)
            if candidate and graph_valid(candidate) and score_total(candidate) > score_total(best):
                best = candidate
    return best, branch_candidates


def _branch_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_u: float,
    side: str,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
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

    branch_aisle = branch_aisle_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_turnaround = branch_turnaround_polygon(site, entrance, heading_degrees, branch_u, side, branch_length)
    branch_drivable = unary_union([branch_aisle, branch_turnaround])
    if not available.covers(branch_drivable):
        diagnostic["reason"] = "branch_geometry_outside_usable_area"
        return None, diagnostic

    kept_main_stalls = [
        stall for stall in base.stalls if not area_overlaps(ShapelyPolygon(stall.polygon), branch_drivable)
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
            polygon=polygon_points(branch_aisle),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="branch",
            parent_aisle_id="A-MAIN",
        ),
        ParkingAisle(
            id="A-BRANCH-001-TURNAROUND",
            polygon=polygon_points(branch_turnaround),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="turnaround",
            parent_aisle_id="A-BRANCH-001",
        ),
    ]
    result = finalize_layout(
        LayoutResult(
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
        )
    )
    diagnostic["stall_count"] = result.stall_count
    diagnostic["graph_valid"] = graph_valid(result)
    diagnostic["graph_errors"] = list(result.graph_validation.get("errors", []))
    if not graph_valid(result):
        diagnostic["reason"] = "branch_invalid_traffic_graph"
        return result, diagnostic
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
        if not module_angle_allowed(90.0, site.stall.allowed_angles):
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
            stall = normalized_local_box_to_world(local, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
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
        candidate = main_aisle_with_turnaround(site, entrance, heading_degrees, start, length)
        if not available.covers(candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal * 1.5)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = main_aisle_with_turnaround(site, entrance, heading_degrees, start, mid)
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
        candidate = branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, length)
        if not available.covers(candidate):
            break
        best = length
        length += step

    low = best
    high = min(length, diagonal)
    for _ in range(16):
        mid = (low + high) / 2
        candidate = branch_with_turnaround(site, entrance, heading_degrees, branch_u, side, mid)
        if available.covers(candidate):
            low = mid
        else:
            high = mid
    return low


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
