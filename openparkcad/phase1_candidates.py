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
from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
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
    inset_depth: float


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
    stall_spec = _main_stall(site)
    if stall_spec.family == "angled":
        return _angled_stalls_along_main_aisle(site, stall_spec, available, entrance, heading_degrees, start_u, end_u)
    if stall_spec.family != "perpendicular":
        return []

    stalls: list[ParkingStall] = []
    u = start_u
    while u + stall_spec.width <= end_u:
        if not module_angle_allowed(90.0, stall_spec.allowed_angles):
            break
        for side in ("left", "right"):
            if side == "left":
                local = (u, site.aisle_width / 2, u + stall_spec.width, site.aisle_width / 2 + stall_spec.length)
            else:
                local = (u, -site.aisle_width / 2 - stall_spec.length, u + stall_spec.width, -site.aisle_width / 2)
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
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.width
    return stalls


def _angled_stalls_along_main_aisle(site: SiteSpec, stall_spec: StallSpec, available, entrance: EntranceSpec, heading_degrees: float, start_u: float, end_u: float) -> list[ParkingStall]:
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []

    stalls: list[ParkingStall] = []
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
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
                        stall_type_id=stall_spec.id,
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
    diagnostic["geometry"] = polygon_points(branch_drivable)
    diagnostic["branch_aisle_geometry"] = polygon_points(branch_aisle)
    diagnostic["branch_turnaround_geometry"] = polygon_points(branch_turnaround)
    diagnostic["branch_turnaround_id"] = f"{branch_id}-TURNAROUND"
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
    diagnostic["generated_stalls"] = _stall_diagnostics(branch_stalls)
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
    stall_spec = _branch_stall(site)
    if stall_spec.family == "angled":
        return _angled_stalls_along_branch(
            site,
            stall_spec,
            available,
            entrance,
            heading_degrees,
            branch_u,
            side,
            branch_id,
            start_t,
            end_t,
            occupied,
            start_index,
        )
    if stall_spec.family != "perpendicular":
        return []

    stalls: list[ParkingStall] = []
    t = start_t
    direction = 1 if side == "left" else -1
    while t + stall_spec.width <= end_t:
        if not module_angle_allowed(90.0, stall_spec.allowed_angles):
            break
        for stall_side in ("left", "right"):
            if stall_side == "left":
                local = (
                    branch_u + site.aisle_width / 2,
                    direction * t,
                    branch_u + site.aisle_width / 2 + stall_spec.length,
                    direction * (t + stall_spec.width),
                )
            else:
                local = (
                    branch_u - site.aisle_width / 2 - stall_spec.length,
                    direction * t,
                    branch_u - site.aisle_width / 2,
                    direction * (t + stall_spec.width),
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
                        stall_type_id=stall_spec.id,
                    )
                )
        t += stall_spec.width
    return stalls


def _angled_stalls_along_branch(
    site: SiteSpec,
    stall_spec: StallSpec,
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
    angle = angled_module_angle(stall_spec.allowed_angles)
    if angle is None:
        return []

    stalls: list[ParkingStall] = []
    theta = math.radians(angle)
    front_pitch = stall_spec.width / math.sin(theta)
    forward_shift = stall_spec.length * math.cos(theta)
    lateral_depth = stall_spec.length * math.sin(theta)
    direction = 1 if side == "left" else -1
    branch_heading = heading_degrees + (90 if side == "left" else -90)
    t = start_t
    while t + front_pitch + forward_shift <= end_t + 1e-9:
        for stall_side in ("left", "right"):
            cross = 1 if stall_side == "left" else -1
            front_u = branch_u + cross * site.aisle_width / 2
            local_points = [
                (front_u, direction * t),
                (front_u, direction * (t + front_pitch)),
                (front_u + cross * lateral_depth, direction * (t + front_pitch + forward_shift)),
                (front_u + cross * lateral_depth, direction * (t + forward_shift)),
            ]
            stall = _local_polygon_to_world(local_points, entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stall_id = f"P-{start_index + len(stalls):03d}"
                stalls.append(
                    ParkingStall(
                        id=stall_id,
                        polygon=polygon_points(stall),
                        angle_degrees=branch_heading + cross * angle,
                        served_by_aisle_id=branch_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        t += front_pitch
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
    stall_spec = _connector_stall(site)
    if stall_spec.family != "perpendicular":
        diagnostics.append(
            {
                "reason": "connectors_not_supported_for_stall_family",
                "stall_family": stall_spec.family,
                "stall_type_id": stall_spec.id,
            }
        )
        return base
    best = base
    for branch_a, branch_b in _connector_pairs(best):
        pair_base = best
        pair_best = best
        for inset_depth in _connector_inset_depths(site, branch_a, branch_b):
            candidate, diagnostic = _connector_layout(
                site,
                available,
                entrance,
                heading_degrees,
                pair_base,
                branch_a,
                branch_b,
                inset_depth,
                finalize_layout,
                graph_valid,
                score_total,
            )
            diagnostics.append(diagnostic)
            if (
                candidate
                and graph_valid(candidate)
                and candidate.stall_count > pair_base.stall_count
                and score_total(candidate) > score_total(pair_best)
            ):
                pair_best = candidate
        best = pair_best
    return best


def _connector_layout(
    site: SiteSpec,
    available,
    entrance: EntranceSpec,
    heading_degrees: float,
    base: LayoutResult,
    branch_a: dict[str, object],
    branch_b: dict[str, object],
    inset_depth: float,
    finalize_layout: FinalizeLayout,
    graph_valid: GraphValid,
    score_total: LayoutScoreTotal,
) -> tuple[LayoutResult | None, dict[str, object]]:
    connector_id = f"A-CONNECTOR-{len(base.selected_connectors) + 1:03d}"
    branch_a_id = str(branch_a["id"])
    branch_b_id = str(branch_b["id"])
    connector_geometry = _connector_geometry(site, entrance, heading_degrees, branch_a, branch_b, inset_depth)
    diagnostic: dict[str, object] = {
        "connector_id": connector_id,
        "connects": [branch_a_id, branch_b_id],
        "connector_inset_depth": float(inset_depth),
        "reason": "connector_evaluated",
    }
    if connector_geometry is None:
        diagnostic["reason"] = "connector_geometry_not_possible"
        return None, diagnostic
    connector = connector_geometry.polygon
    diagnostic["geometry"] = polygon_points(connector)
    diagnostic["connector_inset_depth"] = connector_geometry.inset_depth
    if not available.covers(connector):
        diagnostic["reason"] = "connector_geometry_outside_usable_area"
        return None, diagnostic
    conflict = _connector_conflict(base, connector, {branch_a_id, branch_b_id})
    if conflict:
        diagnostic["reason"] = "connector_overlaps_existing_layout"
        diagnostic["conflict_id"] = conflict
        return None, diagnostic

    connector_drivable = connector
    removed_turnarounds = _connected_turnaround_ids({branch_a_id, branch_b_id})
    kept_aisles = [
        aisle for aisle in base.aisles if aisle.id not in removed_turnarounds
    ]
    kept_aisles = _trim_endpoint_branch_aisles(kept_aisles, connector_drivable, {branch_a_id, branch_b_id})
    occupied_aisles = unary_union(
        [ShapelyPolygon(aisle.polygon) for aisle in kept_aisles]
        + [connector_drivable]
    )
    connector_stalls = _stalls_along_connector(
        site,
        available,
        entrance,
        heading_degrees,
        connector_geometry,
        connector_id,
        occupied_aisles,
        start_index=base.stall_count + 1,
    )
    connector_stall_area = (
        unary_union([ShapelyPolygon(stall.polygon) for stall in connector_stalls])
        if connector_stalls
        else ShapelyPolygon()
    )
    kept_stalls = [
        stall
        for stall in base.stalls
        if not area_overlaps(ShapelyPolygon(stall.polygon), connector_drivable)
        and not area_overlaps(ShapelyPolygon(stall.polygon), connector_stall_area)
    ]
    removed_stalls = base.stall_count - len(kept_stalls)
    diagnostic["generated_stalls"] = _stall_diagnostics(connector_stalls)
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
            "connector_inset_depth": connector_geometry.inset_depth,
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
    stall_spec = _connector_stall(site)
    if not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return stalls

    throat = 0.0 if _connector_l_shape_end_stalls_enabled(site) else _connector_throat_length(site)
    u = connector.u_min + throat
    end_u = connector.u_max - throat
    if end_u - u < stall_spec.width:
        return stalls

    direction = 1 if connector.side == "left" else -1
    half_width = site.aisle_width / 2
    while u + stall_spec.width <= end_u + 1e-9:
        for stall_side in ("outer", "inner"):
            if stall_side == "outer":
                v1 = connector.center_v + direction * half_width
                v2 = connector.center_v + direction * (half_width + stall_spec.length)
            else:
                v1 = connector.center_v - direction * half_width
                v2 = connector.center_v - direction * (half_width + stall_spec.length)
            stall = normalized_local_box_to_world((u, v1, u + stall_spec.width, v2), entrance, heading_degrees)
            if available.covers(stall) and not area_overlaps(occupied, stall):
                stalls.append(
                    ParkingStall(
                        id=f"P-{start_index + len(stalls):03d}",
                        polygon=polygon_points(stall),
                        angle_degrees=heading_degrees,
                        served_by_aisle_id=connector_id,
                        aisle_side=stall_side,
                        stall_type_id=stall_spec.id,
                    )
                )
        u += stall_spec.width
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


def _connector_geometry(site: SiteSpec, entrance: EntranceSpec, heading_degrees: float, branch_a: dict[str, object], branch_b: dict[str, object], inset_depth: float) -> ConnectorGeometry | None:
    u1 = float(branch_a["start_u"])
    u2 = float(branch_b["start_u"])
    if abs(u2 - u1) <= site.aisle_width:
        return None
    side = str(branch_a["side"])
    if side != str(branch_b["side"]):
        return None
    direction = 1 if side == "left" else -1
    length = min(float(branch_a["length"]), float(branch_b["length"]))
    capped_inset = min(max(float(inset_depth), 0.0), _max_connector_inset_depth(site, length))
    center_v = direction * (length - site.aisle_width / 2 - capped_inset)
    u_min = min(u1, u2) - site.aisle_width / 2
    u_max = max(u1, u2) + site.aisle_width / 2
    local = (
        u_min,
        center_v - site.aisle_width / 2,
        u_max,
        center_v + site.aisle_width / 2,
    )
    return ConnectorGeometry(
        polygon=normalized_local_box_to_world(local, entrance, heading_degrees),
        u_min=u_min,
        u_max=u_max,
        center_v=center_v,
        side=side,
        inset_depth=capped_inset,
    )


def _connector_inset_depths(site: SiteSpec, branch_a: dict[str, object], branch_b: dict[str, object]) -> tuple[float, ...]:
    branch_length = min(float(branch_a["length"]), float(branch_b["length"]))
    max_depth = _max_connector_inset_depth(site, branch_length)
    raw = site.optimization.get("connector_inset_depths")
    if isinstance(raw, list):
        depths = []
        for item in raw:
            try:
                depths.append(float(item))
            except (TypeError, ValueError):
                continue
        return _normalized_inset_depths(depths, max_depth)
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return (0.0,)
    stall_spec = _connector_stall(site)
    if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return (0.0,)
    return _normalized_inset_depths(
        [0.0, stall_spec.length / 2, stall_spec.length, stall_spec.length * 1.5],
        max_depth,
    )


def _normalized_inset_depths(depths: list[float], max_depth: float) -> tuple[float, ...]:
    values = {round(min(max(depth, 0.0), max_depth), 6) for depth in depths}
    values.add(0.0)
    return tuple(sorted(values))


def _max_connector_inset_depth(site: SiteSpec, branch_length: float) -> float:
    return max(branch_length - site.aisle_width * 2, 0.0)


def _connector_outer_stall_depth(site: SiteSpec, branch_length: float) -> float:
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return 0.0
    stall_spec = _connector_stall(site)
    if stall_spec.family != "perpendicular" or not module_angle_allowed(90.0, stall_spec.allowed_angles):
        return 0.0
    return min(stall_spec.length, _max_connector_inset_depth(site, branch_length))


def _connector_conflict(layout: LayoutResult, connector, endpoint_branch_ids: set[str]) -> str | None:
    allowed = set(endpoint_branch_ids)
    allowed.update(f"{branch_id}-TURNAROUND" for branch_id in endpoint_branch_ids)
    for aisle in layout.aisles:
        if aisle.id in allowed:
            continue
        if area_overlaps(ShapelyPolygon(aisle.polygon), connector):
            return aisle.id
    return None


def _trim_endpoint_branch_aisles(
    aisles: list[ParkingAisle],
    connector,
    endpoint_branch_ids: set[str],
) -> list[ParkingAisle]:
    main = next((ShapelyPolygon(aisle.polygon) for aisle in aisles if aisle.role == "main"), None)
    if main is None:
        return aisles
    trimmed: list[ParkingAisle] = []
    for aisle in aisles:
        if aisle.id not in endpoint_branch_ids:
            trimmed.append(aisle)
            continue
        geometry = _trim_branch_aisle_to_connector(ShapelyPolygon(aisle.polygon), main, connector)
        if geometry is None:
            trimmed.append(aisle)
            continue
        trimmed.append(
            ParkingAisle(
                id=aisle.id,
                polygon=polygon_points(geometry),
                angle_degrees=aisle.angle_degrees,
                role=aisle.role,
                connected_to_entrance_id=aisle.connected_to_entrance_id,
                parent_aisle_id=aisle.parent_aisle_id,
                connected_aisle_ids=aisle.connected_aisle_ids,
            )
        )
    return trimmed


def _trim_branch_aisle_to_connector(branch, main, connector):
    axis = _branch_axis(branch, main)
    if axis is None:
        return None
    cut = _branch_connector_half_plane(branch, main, connector, axis)
    if cut is None:
        return None
    kept = branch.intersection(cut)
    components = [item for item in getattr(kept, "geoms", [kept]) if getattr(item, "area", 0.0) > 1e-6]
    if not components:
        return None
    return max(components, key=lambda item: item.area)


def _branch_connector_half_plane(branch, main, connector, axis: tuple[float, float]):
    connector_projections = [_project(point, axis) for point in connector.exterior.coords]
    if not connector_projections:
        return None
    main_projection = _project((main.centroid.x, main.centroid.y), axis)
    connector_projection = _project((connector.centroid.x, connector.centroid.y), axis)
    keep_greater = main_projection >= connector_projection
    threshold = max(connector_projections) if keep_greater else min(connector_projections)
    min_x, min_y, max_x, max_y = unary_union([branch, main, connector]).bounds
    span = max(max_x - min_x, max_y - min_y, 1.0) * 4
    return _half_plane(axis, threshold, keep_greater, span)


def _half_plane(axis: tuple[float, float], threshold: float, keep_greater: bool, span: float) -> ShapelyPolygon:
    ax, ay = axis
    px, py = -ay, ax
    origin = (ax * threshold, ay * threshold)
    direction = 1 if keep_greater else -1
    return ShapelyPolygon(
        [
            (origin[0] + px * span, origin[1] + py * span),
            (origin[0] + ax * span * direction + px * span, origin[1] + ay * span * direction + py * span),
            (origin[0] + ax * span * direction - px * span, origin[1] + ay * span * direction - py * span),
            (origin[0] - px * span, origin[1] - py * span),
        ]
    )


def _branch_axis(branch, main) -> tuple[float, float] | None:
    rectangle = branch.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    edges: list[tuple[float, float, float]] = []
    for start, end in zip(coords, coords[1:]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        edges.append((dx * dx + dy * dy, dx, dy))
    if not edges:
        return None
    _, dx, dy = max(edges, key=lambda item: item[0])
    axis = _unit_vector(dx, dy)
    if axis is None:
        return None
    to_main = (main.centroid.x - branch.centroid.x, main.centroid.y - branch.centroid.y)
    if axis[0] * to_main[0] + axis[1] * to_main[1] < 0:
        return (-axis[0], -axis[1])
    return axis


def _unit_vector(dx: float, dy: float) -> tuple[float, float] | None:
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 1e-9:
        return None
    return (dx / length, dy / length)


def _project(point, axis: tuple[float, float]) -> float:
    return float(point[0]) * axis[0] + float(point[1]) * axis[1]


def _connected_turnaround_ids(branch_ids: set[str]) -> set[str]:
    return {f"{branch_id}-TURNAROUND" for branch_id in branch_ids}


def _connector_throat_length(site: SiteSpec) -> float:
    raw = site.optimization.get("connector_throat_length", site.aisle_width)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = site.aisle_width
    return max(value, 0.0)


def _connector_l_shape_end_stalls_enabled(site: SiteSpec) -> bool:
    raw = site.optimization.get(
        "connector_allow_l_shape_end_stalls",
        site.optimization.get("maneuver_l_shape_fallback", True),
    )
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no", "off"}
    return bool(raw)


def _max_clear_aisle_length(site: SiteSpec, available, entrance: EntranceSpec, heading_degrees: float, start: float) -> float:
    min_x, min_y, max_x, max_y = ShapelyPolygon(site.boundary).bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    step = max(_module_step_width(site), 0.5)
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
    step = max(_module_step_width(site), 0.5)
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
    module_width = _module_step_width(site)
    step = float(site.optimization.get("branch_start_step", module_width * 2))
    step = max(step, module_width)
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
            stall_type_id=stall.stall_type_id,
        )
        for index, stall in enumerate(stalls, start=1)
    ]


def _stall_diagnostics(stalls: list[ParkingStall]) -> list[dict[str, object]]:
    return [
        {
            "source_id": stall.id,
            "geometry": stall.polygon,
            "angle_degrees": stall.angle_degrees,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "aisle_side": stall.aisle_side,
            "stall_type_id": stall.stall_type_id,
        }
        for stall in stalls
    ]


def _main_stall(site: SiteSpec) -> StallSpec:
    return site.main_stall or site.stall


def _branch_stall(site: SiteSpec) -> StallSpec:
    return site.branch_stall or site.main_stall or site.stall


def _connector_stall(site: SiteSpec) -> StallSpec:
    return _branch_stall(site)


def _module_step_width(site: SiteSpec) -> float:
    return min(_main_stall(site).width, _branch_stall(site).width)
