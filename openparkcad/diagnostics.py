from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.phase1_support import phase1_unsupported_inputs
from openparkcad.traffic_graph import traffic_graph_summary


def build_input_diagnostics(site: SiteSpec, layout: LayoutResult | None = None) -> dict[str, Any]:
    active_fields = [
        "name",
        "units",
        "site.boundary polygon",
        "site.obstacles polygon",
        "parking.active stall width/length",
        "parking.active stall allowed_angles",
        "aisles.selected width",
        "constraints.setbacks.site_boundary",
    ]
    if _phase1_main_aisle_active(layout):
        active_fields.append("entrance-connected main aisle")
    elif site.entrances:
        active_fields.append("entrances drawn in diagnostics")

    parsed_future_fields: list[str] = []
    warnings: list[str] = []

    if site.standards:
        parsed_future_fields.append("standards")
        warnings.append("standards are parsed as metadata only; legal compliance is not checked yet")
    phase1_active = _phase1_main_aisle_active(layout)

    if site.entrances and not phase1_active:
        parsed_future_fields.append("entrances")
        warnings.append("entrances are parsed and drawn, but aisle connectivity to entrances is not enforced yet")
    elif phase1_active:
        warnings.append("entrance-to-main-aisle connection is active; Phase 2 graph reachability is now reported")
    if site.vehicle:
        parsed_future_fields.append("vehicles.design_vehicle")
        warnings.append("vehicle dimensions are parsed; Phase 3 uses conservative access and turning-sweep envelopes, but full turning radius is not enforced yet")
    if site.site_features:
        parsed_future_fields.append("site_features")
        warnings.append("site_features are parsed but not used for clearance or collision checks yet")
    if site.pedestrian_and_emergency:
        parsed_future_fields.append("pedestrian_and_emergency")
        warnings.append("pedestrian and emergency reservations are parsed but not enforced yet")
    if site.stall.drive_over or site.stall.blocked_sides or site.stall.access_sides != ("front",):
        parsed_future_fields.append("parking.stall access behavior")
        warnings.append("stall access_sides, blocked_sides, and drive_over are parsed but not enforced yet")
    if site.aisle_classes:
        parsed_future_fields.append("aisles.classes")
    if site.aisle_selection_mode != "fixed":
        warnings.append("aisle selection modes beyond fixed are documented but not optimized yet")
    if site.constraints.get("maneuvering"):
        warnings.append("maneuvering constraints are partially active through the Phase 3A stall access envelope")
    if site.optimization:
        parsed_future_fields.append("optimization")
        warnings.append("optimization weights are active for Phase 1 scoring; candidate generation is still deliberately narrow")
    unsupported = phase1_unsupported_inputs(site)
    warnings.extend(f"{item['field']} unsupported in Phase 1: {item['reason']}" for item in unsupported)

    return {
        "source_format": site.source_format,
        "active_fields": active_fields,
        "parsed_future_fields": sorted(set(parsed_future_fields)),
        "warnings": warnings,
        "field_support": _field_support(site, layout),
        "constraint_status": _constraint_status(site, layout),
        "entrances": [asdict(item) for item in site.entrances],
        "vehicle": asdict(site.vehicle) if site.vehicle else None,
        "aisle_selection": {
            "mode": site.aisle_selection_mode,
            "fixed_class": site.fixed_aisle_class,
            "effective_width": site.aisle_width,
        },
        "heading_selection": {
            "selected_heading_degrees": layout.selected_heading_degrees if layout else None,
            "selected_heading_delta_degrees": layout.selected_heading_delta_degrees if layout else None,
            "candidate_heading_deltas_degrees": site.optimization.get("heading_deltas_degrees", [-20, -10, 0, 10, 20]),
            "selected_entrance_offset": layout.selected_entrance_offset if layout else None,
            "candidate_entrance_offsets": site.optimization.get("entrance_offsets", "auto"),
        },
        "branch_selection": {
            "enabled": site.optimization.get("enable_branches", True),
            "selected_side": layout.selected_branch_side if layout else None,
            "selected_start_u": layout.selected_branch_start_u if layout else None,
            "selected_length": layout.selected_branch_length if layout else None,
            "selected_branches": layout.selected_branches if layout else [],
            "selected_connectors": layout.selected_connectors if layout else [],
            "candidate_start_positions": site.optimization.get("branch_start_positions", "auto"),
            "branch_sides": site.optimization.get("branch_sides", ["left", "right"]),
            "branch_start_step": site.optimization.get("branch_start_step", "auto"),
            "max_branches": site.optimization.get("max_branches", 2),
            "enable_connectors": site.optimization.get("enable_connectors", True),
        },
        "score": layout.score if layout else {},
        "maneuver_validation": layout.maneuver_validation if layout else None,
        "active_stall_type": asdict(site.stall),
        "unsupported_phase1_inputs": unsupported,
        "stall_access": _stall_access(layout),
        "aisle_connectivity": _aisle_connectivity(layout),
        "traffic_graph": traffic_graph_summary(layout) if layout else None,
    }


def _constraint_status(site: SiteSpec, layout: LayoutResult | None) -> list[dict[str, str]]:
    entrance_status = {
        "constraint": "entrance to main aisle",
        "status": "active" if _phase1_main_aisle_active(layout) else "future",
        "note": (
            f"Main aisle starts from entrance {layout.main_entrance_id}."
            if _phase1_main_aisle_active(layout)
            else "Entrances are parsed and drawn but not connected to aisle graph yet."
        ),
    }
    turnaround_status = {
        "constraint": "dead-end turnaround",
        "status": "active" if _phase1_main_aisle_active(layout) else "future",
        "note": (
            "A conservative turnaround pad is reserved at the end of each generated dead-end aisle."
            if _phase1_main_aisle_active(layout)
            else "Turnaround geometry is only created by the Phase 1 main aisle generator."
        ),
    }
    return [
        {
            "constraint": "geometry containment",
            "status": "active",
            "note": "Generated stall and aisle access polygons are checked against usable area.",
        },
        {
            "constraint": "obstacle avoidance",
            "status": "active",
            "note": "Polygon obstacles are removed from the usable area.",
        },
        entrance_status,
        turnaround_status,
        {
            "constraint": "stall-to-aisle association",
            "status": "active" if _all_stalls_have_aisles(layout) else "future",
            "note": (
                "Each generated stall records the aisle that serves its front access side."
                if _all_stalls_have_aisles(layout)
                else "Stall-to-aisle association is only available after Phase 1 layout generation."
            ),
        },
        {
            "constraint": "phase1 aisle connectivity",
            "status": "active" if _phase1_main_aisle_active(layout) else "future",
            "note": (
                "Generated Phase 1 aisles are main/turnaround/branch pieces attached to the entrance-connected main aisle."
                if _phase1_main_aisle_active(layout)
                else "Full aisle graph reachability is not implemented yet."
            ),
        },
        {
            "constraint": "full aisle graph reachability",
            "status": _traffic_graph_status(layout),
            "note": (
                "Phase 2A validates generated aisle and stall reachability from the traffic graph."
                if _traffic_graph_status(layout) == "active"
                else "Traffic graph reachability is only available after layout generation."
            ),
        },
        {
            "constraint": "stall maneuver access envelope",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3A checks a conservative rectangular access envelope from each stall front into its serving aisle."
                if _maneuver_status(layout) == "active"
                else "Maneuver access envelope checks are only available after layout generation."
            ),
        },
        {
            "constraint": "stall turning sweep proxy",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3B checks an expanded aisle-side envelope near each stall front to approximate low-speed turning clearance."
                if _maneuver_status(layout) == "active"
                else "Turning sweep proxy checks are only available after layout generation."
            ),
        },
        {
            "constraint": "maneuver rule dispatch",
            "status": _maneuver_status(layout),
            "note": (
                "Phase 3C-1 dispatches stalls to explicit active or future maneuver rules."
                if _maneuver_status(layout) == "active"
                else "Maneuver rule dispatch is only available after layout generation."
            ),
        },
        {
            "constraint": "vehicle turning radius",
            "status": "future",
            "note": "Vehicle data is parsed but swept path and turning-radius checks are not implemented yet.",
        },
        {
            "constraint": "narrow two-way deadlock",
            "status": "future",
            "note": "Narrow two-way aisles should remain disabled until graph deadlock checks exist.",
        },
        {
            "constraint": "pedestrian and emergency reservations",
            "status": "future",
            "note": "Reserved routes are parsed but not enforced as no-parking areas yet.",
        },
    ]


def _field_support(site: SiteSpec, layout: LayoutResult | None) -> dict[str, str]:
    support = {
        "version": "active",
        "name": "active",
        "units": "active",
        "standards": "parsed_not_enforced",
        "site.boundary.polygon": "active",
        "site.boundary.curve_loop": "future",
        "site.obstacles.polygon": "active",
        "site.reserved_areas": "future",
        "site_features": "drawn_not_enforced" if site.site_features else "future",
        "entrances": "active" if _phase1_main_aisle_active(layout) else ("drawn_not_enforced" if site.entrances else "future"),
        "pedestrian_and_emergency.pedestrian_routes": _pedestrian_status(site, "pedestrian_routes"),
        "pedestrian_and_emergency.fire_lanes": _pedestrian_status(site, "fire_lanes"),
        "vehicles.design_vehicle": "parsed_not_enforced" if site.vehicle else "future",
        "parking.standard_perpendicular": "active",
        "parking.angled_parallel_t_end": "future",
        "parking.maneuver_rule_dispatch": _maneuver_status(layout),
        "parking.drive_over": "parsed_not_enforced",
        "parking.access_sides": "parsed_not_enforced",
        "parking.blocked_sides": "parsed_not_enforced",
        "aisles.fixed_wide_two_way": "active",
        "aisles.heading_candidate_selection": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.entrance_offset_selection": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.single_branch_candidate": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.multiple_branch_candidates": "active" if _phase1_main_aisle_active(layout) else "future",
        "aisles.narrow_one_way": "future",
        "aisles.narrow_two_way": "future",
        "constraints.geometry_containment": "active",
        "constraints.entrance_to_main_aisle": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.dead_end_turnaround": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.stall_to_aisle_association": "active" if _all_stalls_have_aisles(layout) else "future",
        "constraints.phase1_aisle_connectivity": "active" if _phase1_main_aisle_active(layout) else "future",
        "constraints.full_aisle_graph_reachability": _traffic_graph_status(layout),
        "constraints.maneuver_access_envelope": _maneuver_status(layout),
        "constraints.turning_sweep_proxy": _maneuver_status(layout),
        "constraints.maneuver_rule_dispatch": _maneuver_status(layout),
        "constraints.turning_radius": "future",
        "constraints.swept_path": "future",
        "optimization.weights": "parsed_not_enforced" if site.optimization else "future",
        "optimization.score_breakdown": "active" if _phase1_main_aisle_active(layout) else "future",
        "diagnostics.report": "active",
        "diagnostics.debug_layers": "active",
    }
    return support


def _pedestrian_status(site: SiteSpec, key: str) -> str:
    if site.pedestrian_and_emergency.get(key):
        return "drawn_not_enforced"
    return "future"


def _phase1_main_aisle_active(layout: LayoutResult | None) -> bool:
    return bool(layout and layout.generation_mode == "phase1_main_aisle" and layout.main_entrance_id)


def _all_stalls_have_aisles(layout: LayoutResult | None) -> bool:
    return bool(layout and layout.stalls and all(stall.served_by_aisle_id for stall in layout.stalls))


def _stall_access(layout: LayoutResult | None) -> list[dict[str, str | None]]:
    if not layout:
        return []
    return [
        {
            "stall_id": stall.id,
            "served_by_aisle_id": stall.served_by_aisle_id,
            "aisle_side": stall.aisle_side,
        }
        for stall in layout.stalls
    ]


def _aisle_connectivity(layout: LayoutResult | None) -> list[dict[str, str | None]]:
    if not layout:
        return []
    return [
        {
            "aisle_id": aisle.id,
            "role": aisle.role,
            "connected_to_entrance_id": aisle.connected_to_entrance_id,
            "parent_aisle_id": aisle.parent_aisle_id,
            "connected_aisle_ids": ",".join(aisle.connected_aisle_ids) if aisle.connected_aisle_ids else None,
        }
        for aisle in layout.aisles
    ]


def _traffic_graph_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    return "active" if traffic_graph_summary(layout)["valid"] else "active_failed"


def _maneuver_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    validation = layout.maneuver_validation
    if not validation:
        return "future"
    return "active" if validation.get("valid") else "active_failed"
