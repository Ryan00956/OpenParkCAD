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
    if layout and layout.operational_quality:
        mode = layout.operational_quality.get("mode", "score_only")
        warnings.append(f"operational quality checks are active in {mode} mode for Phase 5E")
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
            "connector_allow_outer_stall_row": site.optimization.get("connector_allow_outer_stall_row", True),
            "connector_inset_depths": site.optimization.get("connector_inset_depths", "auto"),
        },
        "stall_type_selection": {
            "selected_stall_type_id": layout.selected_stall_type_id if layout else site.stall.id,
            "selected_stall_assignment": layout.selected_stall_assignment if layout else {},
            "active_stall_type_id": site.stall.id,
            "candidate_stall_type_ids": [stall.id for stall in _stall_candidates(site)],
            "attempts": layout.stall_type_attempts if layout else [],
            "assignment_attempts": layout.stall_assignment_attempts if layout else [],
        },
        "score": layout.score if layout else {},
        "candidate_snapshot": {
            "version": "phase4b-1",
            "object_count": len(layout.candidate_objects) if layout else 0,
            "conflict_count": _candidate_conflict_count(layout),
            "connector_candidate_count": _connector_candidate_count(layout),
            "synthetic_connector_candidate_count": _synthetic_connector_candidate_count(layout),
            "selection_version": layout.candidate_selection.get("version") if layout else None,
            "selection_status": layout.candidate_selection.get("status") if layout else None,
            "selection_selected_count": layout.candidate_selection.get("selected_count", 0) if layout else 0,
            "selection_selected_branch_count": layout.candidate_selection.get("selected_branch_count", 0) if layout else 0,
            "selection_selected_connector_count": layout.candidate_selection.get("selected_connector_count", 0) if layout else 0,
            "selection_selected_bundle_count": layout.candidate_selection.get("selected_bundle_count", 0) if layout else 0,
            "active": bool(layout and layout.candidate_objects),
        },
        "candidate_network_preview": {
            "version": layout.candidate_network_preview.get("version") if layout else None,
            "status": layout.candidate_network_preview.get("status") if layout else None,
            "aisle_count": layout.candidate_network_preview.get("aisle_count", 0) if layout else 0,
            "shadow_aisle_count": layout.candidate_network_preview.get("shadow_aisle_count", 0) if layout else 0,
            "shadow_turnaround_count": layout.candidate_network_preview.get("shadow_turnaround_count", 0) if layout else 0,
            "connector_count": layout.candidate_network_preview.get("connector_count", 0) if layout else 0,
            "loop_connector_count": layout.candidate_network_preview.get("loop_connector_count", 0) if layout else 0,
            "suppressed_turnaround_count": layout.candidate_network_preview.get("suppressed_turnaround_count", 0) if layout else 0,
            "valid_no_internal_conflicts": layout.candidate_network_preview.get("valid_no_internal_conflicts") if layout else None,
            "validation_valid": _preview_validation_valid(layout),
            "validation_errors": _preview_validation_errors(layout),
            "active": bool(layout and layout.candidate_network_preview),
        },
        "candidate_layout_preview": {
            "version": layout.candidate_layout_preview.get("version") if layout else None,
            "status": layout.candidate_layout_preview.get("status") if layout else None,
            "aisle_count": layout.candidate_layout_preview.get("aisle_count", 0) if layout else 0,
            "stall_count": layout.candidate_layout_preview.get("stall_count", 0) if layout else 0,
            "score_total": _layout_preview_score_total(layout),
            "score_delta": _layout_preview_score_delta(layout),
            "promotion_eligible": _layout_preview_promotion_eligible(layout),
            "validation_valid": _layout_preview_validation_valid(layout),
            "validation_errors": _layout_preview_validation_errors(layout),
            "active": bool(layout and layout.candidate_layout_preview),
        },
        "candidate_layout_promotion": layout.candidate_layout_promotion if layout else {},
        "maneuver_validation": layout.maneuver_validation if layout else None,
        "operational_quality": layout.operational_quality if layout else None,
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
            "constraint": "angled stall maneuver proxy",
            "status": _angled_maneuver_status(site, layout),
            "note": (
                "Phase 3C-2 can validate angled stall access envelopes, but angled stall generation is still future work."
                if _angled_maneuver_status(site, layout) == "active"
                else "Angled stall maneuver validation is available only when the active stall family is angled."
            ),
        },
        {
            "constraint": "operational quality risk report",
            "status": _operational_quality_status(layout),
            "note": (
                "Phase 5E reports junction, entrance-throat, stall-route risks, and route-summary threshold risks; configured modes can score, gate promotion, or hard-reject risky layouts."
                if _operational_quality_status(layout) == "active"
                else "Operational quality checks are only available after layout generation."
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
        "parking.stall_type_candidate_selection": "active" if len(_stall_candidates(site)) > 1 else "available",
        "parking.stall_type_segment_assignment": "active" if layout and layout.stall_assignment_attempts else ("available" if len(_stall_candidates(site)) > 1 else "future"),
        "parking.angled_maneuver_proxy": _angled_maneuver_status(site, layout),
        "parking.angled_main_aisle_generation": _angled_generation_status(site, layout),
        "parking.angled_branch_generation": _angled_branch_generation_status(site, layout),
        "parking.angled_connector_generation": "future",
        "parking.parallel_t_end": "future",
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
        "constraints.maneuver_l_shape_fallback": _maneuver_status(layout),
        "constraints.maneuver_rule_dispatch": _maneuver_status(layout),
        "constraints.operational_quality": _operational_quality_status(layout),
        "constraints.junction_conflict_points": _operational_quality_status(layout),
        "constraints.entrance_throat_blockage": _operational_quality_status(layout),
        "constraints.operational_route_risk": _operational_quality_status(layout),
        "constraints.operational_route_summary": _operational_quality_status(layout),
        "constraints.turning_radius": "future",
        "constraints.swept_path": "future",
        "optimization.weights": "parsed_not_enforced" if site.optimization else "future",
        "optimization.score_breakdown": "active" if _phase1_main_aisle_active(layout) else "future",
        "optimization.candidate_objects": "active" if layout and layout.candidate_objects else "future",
        "optimization.candidate_conflict_matrix": "active" if layout and layout.candidate_objects else "future",
        "optimization.shadow_candidate_selector": "active" if layout and layout.candidate_selection else "future",
        "optimization.candidate_network_preview": "active" if layout and layout.candidate_network_preview else "future",
        "optimization.candidate_shadow_branch_turnarounds": "active" if _shadow_turnarounds_active(layout) else "future",
        "optimization.connector_inset_depths": "active" if layout and site.optimization.get("enable_connectors", True) else "future",
        "optimization.connector_l_shape_end_stalls": "active" if layout and site.optimization.get("enable_connectors", True) else "future",
        "optimization.operational_risk_weight": "active" if layout else "future",
        "optimization.operational_quality_mode": "active" if layout else "future",
        "optimization.operational_max_risk_score": "active" if layout else "future",
        "optimization.operational_max_route_length": "available" if layout else "future",
        "optimization.operational_turnaround_dependency_risk": "available" if layout else "future",
        "optimization.operational_max_turnaround_dependency_ratio": "available" if layout else "future",
        "optimization.operational_turnaround_dependency_ratio_risk": "available" if layout else "future",
        "optimization.candidate_layout_preview": "active" if layout and layout.candidate_layout_preview else "future",
        "optimization.candidate_layout_preview_scoring": "active" if _layout_preview_comparison(layout) else "future",
        "optimization.promote_candidate_layout_preview": _candidate_layout_promotion_status(layout),
        "diagnostics.report": "active",
        "diagnostics.svg_candidate_network_preview": "active" if layout and layout.candidate_network_preview else "future",
        "diagnostics.debug_layers": "active",
    }
    return support


def _pedestrian_status(site: SiteSpec, key: str) -> str:
    if site.pedestrian_and_emergency.get(key):
        return "drawn_not_enforced"
    return "future"


def _stall_candidates(site: SiteSpec):
    return site.stall_candidates or (site.stall,)


def _phase1_main_aisle_active(layout: LayoutResult | None) -> bool:
    return bool(
        layout
        and layout.generation_mode in {"phase1_main_aisle", "candidate_layout_promoted"}
        and layout.main_entrance_id
    )


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
            "stall_type_id": stall.stall_type_id,
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


def _candidate_conflict_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return sum(len(candidate.conflict_ids) for candidate in layout.candidate_objects) // 2


def _connector_candidate_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return len([candidate for candidate in layout.candidate_objects if candidate.kind == "aisle_skeleton" and candidate.role == "connector"])


def _synthetic_connector_candidate_count(layout: LayoutResult | None) -> int:
    if not layout:
        return 0
    return len(
        [
            candidate
            for candidate in layout.candidate_objects
            if candidate.kind == "aisle_skeleton"
            and candidate.role == "connector"
            and candidate.metadata.get("synthetic")
        ]
    )


def _preview_validation_valid(layout: LayoutResult | None) -> bool | None:
    if not layout:
        return None
    validation = layout.candidate_network_preview.get("validation")
    if not isinstance(validation, dict):
        return None
    return bool(validation.get("valid"))


def _preview_validation_errors(layout: LayoutResult | None) -> list[str]:
    if not layout:
        return []
    validation = layout.candidate_network_preview.get("validation")
    if not isinstance(validation, dict):
        return []
    errors = validation.get("errors", [])
    if not isinstance(errors, list):
        return []
    return [str(item) for item in errors]


def _layout_preview_validation_valid(layout: LayoutResult | None) -> bool | None:
    if not layout:
        return None
    validation = layout.candidate_layout_preview.get("validation")
    if not isinstance(validation, dict):
        return None
    return bool(validation.get("valid"))


def _layout_preview_validation_errors(layout: LayoutResult | None) -> list[str]:
    if not layout:
        return []
    validation = layout.candidate_layout_preview.get("validation")
    if not isinstance(validation, dict):
        return []
    errors = validation.get("errors", [])
    if not isinstance(errors, list):
        return []
    return [str(item) for item in errors]


def _layout_preview_comparison(layout: LayoutResult | None) -> dict[str, Any]:
    if not layout:
        return {}
    comparison = layout.candidate_layout_preview.get("comparison")
    return comparison if isinstance(comparison, dict) else {}


def _layout_preview_score_total(layout: LayoutResult | None) -> float | None:
    if not layout:
        return None
    score = layout.candidate_layout_preview.get("score")
    if not isinstance(score, dict) or not isinstance(score.get("total"), int | float):
        return None
    return float(score["total"])


def _layout_preview_score_delta(layout: LayoutResult | None) -> float | None:
    comparison = _layout_preview_comparison(layout)
    value = comparison.get("score_delta")
    return float(value) if isinstance(value, int | float) else None


def _layout_preview_promotion_eligible(layout: LayoutResult | None) -> bool | None:
    comparison = _layout_preview_comparison(layout)
    value = comparison.get("promotion_eligible")
    return bool(value) if isinstance(value, bool) else None


def _candidate_layout_promotion_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    if layout.candidate_layout_promotion.get("status") == "promoted":
        return "active"
    if layout.candidate_layout_promotion.get("requested"):
        return "active_rejected"
    return "available"


def _shadow_turnarounds_active(layout: LayoutResult | None) -> bool:
    if not layout:
        return False
    count = layout.candidate_network_preview.get("shadow_turnaround_count")
    return isinstance(count, int | float) and count > 0


def _maneuver_status(layout: LayoutResult | None) -> str:
    if not layout:
        return "future"
    validation = layout.maneuver_validation
    if not validation:
        return "future"
    return "active" if validation.get("valid") else "active_failed"


def _operational_quality_status(layout: LayoutResult | None) -> str:
    if not layout or not layout.operational_quality:
        return "future"
    return "active" if layout.operational_quality.get("valid", True) else "active_failed"


def _angled_maneuver_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "angled"):
        return "available"
    return _maneuver_status(layout)


def _angled_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not _has_active_stall_family(site, layout, "angled"):
        return "available"
    if not layout:
        return "future"
    return "active" if layout.stall_count > 0 else "active_failed"


def _angled_branch_generation_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    branch_stall = layout.site.branch_stall if layout else site.branch_stall
    if (branch_stall or site.stall).family != "angled":
        return "available"
    if not layout:
        return "future"
    return "active" if layout.selected_branches else "available"


def _has_active_stall_family(site: SiteSpec, layout: LayoutResult | None, family: str) -> bool:
    active_site = layout.site if layout else site
    return any(
        stall.family == family
        for stall in (active_site.main_stall, active_site.branch_stall, active_site.stall)
        if stall is not None
    )
