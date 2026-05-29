from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import math

from shapely import affinity
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
    angled_module_angle,
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


@dataclass(frozen=True)
class ConnectorGeometry:
    polygon: object
    u_min: float
    u_max: float
    center_v: float
    side: str


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
    if site.stall.family == "angled":
        return _angled_stalls_along_main_aisle(site, available, entrance, heading_degrees, start_u, end_u)

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


def _angled_stalls_along_main_aisle(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start_u: float, end_u: float) -> list[ParkingStall]:
    angle = angled_module_angle(site.stall.allowed_angles)
    if angle is None:
        return []

    stalls: list[ParkingStall] = []
    theta = math.radians(angle)
    front_pitch = site.stall.width / math.sin(theta)
    forward_shift = site.stall.length * math.cos(theta)
    lateral_depth = site.stall.length * math.sin(theta)
    u = start_u
    while u + front_pitch + forward_shift <= end_u + 1e-9:
        for side in ("left", "right"):
            direction = 1 if side == "left" else -1
            front_v = direction * site.aisle_width / 2
            local_points = [
                (u, front_v),
                (u + front_pitch, front_v),
                (u + front_pitch + forward_shift, front_v + direction * lateral_depth),
                (u + forward_shift, front_v + direction * lateral_depth),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall):
                stall_id = f"P-{len(stalls) + 1:03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees + direction * angle,
                        served_by_aisle_id="A-MAIN",
                        aisle_side=side,
                    )
                )
        u += front_pitch
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
    if site.stall.family != "perpendicular":
        return base, [
            {
                "reason": "branches_not_supported_for_stall_family",
                "stall_family": site.stall.family,
            }
        ]

    best = base
    branch_candidates: list[dict[str, object]] = []
    max_branches = _max_branches(site)
    for iteration in range(1, max_branches + 1):
        round_best = best
        for branch_u in _branch_start_positions(site, main_aisle_length):
            for side in _branch_sides(site):
                branch_index = _next_branch_index(best)
                candidate, diagnostic = _branch_layout(
                    site,
                    available,
                    entrance,
                    heading_degrees,
                    best,
                    branch_u,
                    side,
                    branch_index,
                    finalize_layout,
                    graph_valid,
                    score_total,
                )
                diagnostic["iteration"] = iteration
                branch_candidates.append(diagnostic)
                if candidate and graph_valid(candidate) and score_total(candidate) > score_total(round_best):
                    round_best = candidate
        if round_best is best:
            break
        best = round_best
    best = _with_best_connectors(site, available, entrance, heading_degrees, best, finalize_layout, graph_valid, score_total, branch_candidates)
    return best, branch_candidates


def _branch_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_u: float,
    side: str,
    branch_index: int,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult | None, dict[str, object]]:
    branch_id = f"A-BRANCH-{branch_index:03d}"
    branch_length = _max_clear_branch_length(site, available, entrance, heading_degrees, branch_u, side)
    diagnostic: dict[str, object] = {
        "branch_id": branch_id,
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
    conflict = _branch_conflict(base, branch_drivable)
    if conflict:
        diagnostic["reason"] = "branch_overlaps_existing_layout"
        diagnostic["conflict_id"] = conflict
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
        branch_id,
        start_t=site.aisle_width,
        end_t=branch_length - site.aisle_width,
        occupied=occupied,
        start_index=len(kept_main_stalls) + 1,
    )
    stalls = _renumber_stalls(kept_main_stalls + branch_stalls)
    aisles = list(base.aisles) + [
        ParkingAisle(
            id=branch_id,
            polygon=polygon_points(branch_aisle),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="branch",
            parent_aisle_id="A-MAIN",
        ),
        ParkingAisle(
            id=f"{branch_id}-TURNAROUND",
            polygon=polygon_points(branch_turnaround),
            angle_degrees=heading_degrees + (90 if side == "left" else -90),
            role="turnaround",
            parent_aisle_id=branch_id,
        ),
    ]
    selected_branches = [
        *base.selected_branches,
        {
            "id": branch_id,
            "side": side,
            "start_u": branch_u,
            "length": branch_length,
        },
    ]
    first_branch = selected_branches[0]
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
            selected_branch_side=str(first_branch["side"]),
            selected_branch_start_u=float(first_branch["start_u"]),
            selected_branch_length=float(first_branch["length"]),
            selected_branches=selected_branches,
            unsupported_phase1_inputs=base.unsupported_phase1_inputs,
        )
    )
    diagnostic["stall_count"] = result.stall_count
    diagnostic["graph_valid"] = graph_valid(result)
    diagnostic["graph_errors"] = list(result.graph_validation.get("errors", []))
    if not graph_valid(result):
        diagnostic["reason"] = "branch_invalid_traffic_graph"
        return result, diagnostic
    if score_total(result) <= score_total(base):
        diagnostic["reason"] = "branch_does_not_improve_score"
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
    branch_id: str,
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
                        served_by_aisle_id=branch_id,
                        aisle_side=stall_side,
                    )
                )
        t += site.stall.width
    return stalls


def _with_best_connectors(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
    diagnostics: list[dict[str, object]],
) -> LayoutResult:
    if not site.optimization.get("enable_connectors", True):
        return base
    best = base
    for branch_a, branch_b in _connector_pairs(best):
        candidate, diagnostic = _connector_layout(site, available, entrance, heading_degrees, best, branch_a, branch_b, finalize_layout, graph_valid, score_total)
        diagnostics.append(diagnostic)
        if candidate and graph_valid(candidate) and score_total(candidate) > score_total(best):
            best = candidate
    return best


def _connector_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult | None, dict[str, object]]:
    connector_id = f"A-CONNECTOR-{len(base.selected_connectors) + 1:03d}"
    branch_a_id = str(branch_a["id"])
    branch_b_id = str(branch_b["id"])
    connector_geometry = _connector_geometry(site, entrance, heading_degrees, branch_a, branch_b)
    diagnostic: dict[str, object] = {
        "connector_id": connector_id,
        "connects": [branch_a_id, branch_b_id],
        "reason": "connector_evaluated",
    }
    if connector_geometry is None:
        diagnostic["reason"] = "connector_geometry_not_possible"
        return None, diagnostic
    connector = connector_geometry.polygon
    if not available.covers(connector):
        diagnostic["reason"] = "connector_geometry_outside_usable_area"
        return None, diagnostic
    conflict = _connector_conflict(base, connector, {branch_a_id, branch_b_id})
    if conflict:
        diagnostic["reason"] = "connector_overlaps_existing_layout"
        diagnostic["conflict_id"] = conflict
        return None, diagnostic

    connector_drivable = connector
    kept_stalls = [
        stall for stall in base.stalls if not area_overlaps(ShapelyPolygon(stall.polygon), connector_drivable)
    ]
    removed_stalls = base.stall_count - len(kept_stalls)
    removed_turnarounds = _connected_turnaround_ids({branch_a_id, branch_b_id})
    kept_aisles = [
        aisle for aisle in base.aisles if aisle.id not in removed_turnarounds
    ]
    occupied = unary_union(
        [ShapelyPolygon(aisle.polygon) for aisle in kept_aisles]
        + [connector_drivable]
        + [ShapelyPolygon(stall.polygon) for stall in kept_stalls]
    )
    connector_stalls = _stalls_along_connector(
        site,
        available,
        entrance,
        heading_degrees,
        connector_geometry,
        connector_id,
        occupied,
        start_index=len(kept_stalls) + 1,
    )
    connector_aisle = ParkingAisle(
        id=connector_id,
        polygon=polygon_points(connector_drivable),
        angle_degrees=heading_degrees,
        role="connector",
        parent_aisle_id=branch_a_id,
        connected_aisle_ids=(branch_b_id,),
    )
    selected_connectors = [
        *base.selected_connectors,
        {
            "id": connector_id,
            "connects": [branch_a_id, branch_b_id],
            "removed_stalls": removed_stalls,
            "added_stalls": len(connector_stalls),
            "removed_turnarounds": sorted(removed_turnarounds),
        },
    ]
    result = finalize_layout(
        LayoutResult(
            site=site,
            stalls=_renumber_stalls(kept_stalls + connector_stalls),
            aisles=[*kept_aisles, connector_aisle],
            selected_angle_degrees=base.selected_angle_degrees,
            generation_mode=base.generation_mode,
            main_entrance_id=base.main_entrance_id,
            selected_heading_degrees=base.selected_heading_degrees,
            selected_heading_delta_degrees=base.selected_heading_delta_degrees,
            selected_entrance_offset=base.selected_entrance_offset,
            selected_branch_side=base.selected_branch_side,
            selected_branch_start_u=base.selected_branch_start_u,
            selected_branch_length=base.selected_branch_length,
            selected_branches=base.selected_branches,
            selected_connectors=selected_connectors,
            unsupported_phase1_inputs=base.unsupported_phase1_inputs,
        )
    )
    diagnostic["stall_count"] = result.stall_count
    diagnostic["removed_stalls"] = removed_stalls
    diagnostic["added_stalls"] = len(connector_stalls)
    diagnostic["removed_turnarounds"] = sorted(removed_turnarounds)
    diagnostic["graph_valid"] = graph_valid(result)
    diagnostic["graph_errors"] = list(result.graph_validation.get("errors", []))
    if not graph_valid(result):
        diagnostic["reason"] = "connector_invalid_traffic_graph"
        return result, diagnostic
    if score_total(result) <= score_total(base):
        diagnostic["reason"] = "connector_does_not_improve_score"
        return result, diagnostic
    diagnostic["reason"] = "connector_improves_score"
    return result, diagnostic


def _stalls_along_connector(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    connector: ConnectorGeometry,
    connector_id: str,
    occupied,
    start_index: int,
) -> list[ParkingStall]:
    stalls: list[ParkingStall] = []
    if not module_angle_allowed(90.0, site.stall.allowed_angles):
        return stalls

    throat = _connector_throat_length(site)
    u = connector.u_min + throat
    end_u = connector.u_max - throat
    if end_u - u < site.stall.width:
        return stalls

    direction = 1 if connector.side == "left" else -1
    half_width = site.aisle_width / 2
    while u + site.stall.width <= end_u + 1e-9:
        for stall_side in ("outer", "inner"):
            if stall_side == "outer":
                v1 = connector.center_v + direction * half_width
                v2 = connector.center_v + direction * (half_width + site.stall.length)
            else:
                v1 = connector.center_v - direction * half_width
                v2 = connector.center_v - direction * (half_width + site.stall.length)
            stall = normalized_local_box_to_world((u, v1, u + site.stall.width, v2), entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                    )
                )
        u += site.stall.width
    return stalls


def _connector_pairs(layout: LayoutResult) -> list[tuple[dict[str, object], dict[str, object]]]:
    pairs: list[tuple[dict[str, object], dict[str, object]]] = []
    for side in ("left", "right"):
        branches = sorted(
            [branch for branch in layout.selected_branches if branch.get("side") == side],
            key=lambda item: float(item["start_u"]),
        )
        for first, second in zip(branches, branches[1:]):
            pairs.append((first, second))
    return pairs


def _connector_geometry(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_a: dict[str, object], branch_b: dict[str, object]) -> ConnectorGeometry | None:
    u1 = float(branch_a["start_u"])
    u2 = float(branch_b["start_u"])
    if abs(u2 - u1) <= site.aisle_width:
        return None
    side = str(branch_a["side"])
    if side != str(branch_b["side"]):
        return None
    direction = 1 if side == "left" else -1
    length = min(float(branch_a["length"]), float(branch_b["length"]))
    center_v = direction * (length - site.aisle_width / 2)
    local = (
        min(u1, u2),
        center_v - site.aisle_width / 2,
        max(u1, u2),
        center_v + site.aisle_width / 2,
    )
    return ConnectorGeometry(
        polygon=normalized_local_box_to_world(local, entrance, heading_degrees),
        u_min=min(u1, u2),
        u_max=max(u1, u2),
        center_v=center_v,
        side=side,
    )


def _connector_conflict(layout: LayoutResult, connector, endpoint_branch_ids: set[str]) -> str | None:
    allowed = set(endpoint_branch_ids)
    allowed.update(f"{branch_id}-TURNAROUND" for branch_id in endpoint_branch_ids)
    for aisle in layout.aisles:
        if aisle.id in allowed:
            continue
        if area_overlaps(ShapelyPolygon(aisle.polygon), connector):
            return aisle.id
    return None


def _connected_turnaround_ids(branch_ids: set[str]) -> set[str]:
    return {f"{branch_id}-TURNAROUND" for branch_id in branch_ids}


def _connector_throat_length(site: SiteSpec) -> float:
    raw = site.optimization.get("connector_throat_length", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


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


def _branch_sides(site: SiteSpec) -> tuple[str, ...]:
    raw = site.optimization.get("branch_sides")
    if isinstance(raw, list):
        sides = tuple(str(item) for item in raw if str(item) in {"left", "right"})
        return sides or ("left", "right")
    return ("left", "right")


def _max_branches(site: SiteSpec) -> int:
    raw = site.optimization.get("max_branches", 2)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    return max(value, 0)


def _next_branch_index(layout: LayoutResult) -> int:
    existing = [aisle for aisle in layout.aisles if aisle.role == "branch"]
    return len(existing) + 1


def _branch_conflict(layout: LayoutResult, branch_drivable) -> str | None:
    for aisle in layout.aisles:
        if aisle.id == "A-MAIN":
            continue
        if area_overlaps(ShapelyPolygon(aisle.polygon), branch_drivable):
            return aisle.id
    for stall in layout.stalls:
        if stall.served_by_aisle_id == "A-MAIN":
            continue
        if area_overlaps(ShapelyPolygon(stall.polygon), branch_drivable):
            return stall.id
    return None


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


def _local_polygon_to_world(points: list[tuple[float, float]], entrance: EntranceSpec, heading_degrees: float):
    geometry = ShapelyPolygon(points)
    rotated = affinity.rotate(geometry, heading_degrees, origin=(0, 0), use_radians=False)
    return affinity.translate(rotated, xoff=entrance.center[0], yoff=entrance.center[1])


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
