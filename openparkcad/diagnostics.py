from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.phase1_support import phase1_unsupported_inputs
from openparkcad.site_constraints import declared_constraint_geometries
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
    vehicle_mode = _vehicle_validation_mode(site, layout)
    vehicle_status = _vehicle_field_status(site, layout)
    if site.vehicle:
        active_fields.append("vehicles.design_vehicle")
        if vehicle_mode == "exact_swept_path":
            warnings.append(
                f"vehicle validation {'uses' if layout else 'requests'} the supported perpendicular-90 reverse-in "
                "bicycle template with exact "
                "constant-curvature pose integration and a conservative sampled body envelope; this is not a driver, "
                "tyre, dynamics, or jurisdictional simulation"
            )
        elif vehicle_mode == "conservative_analytic":
            warnings.append(
                f"vehicle validation {'uses' if layout else 'requests'} rear-axle turning-radius conversion and "
                "conservative analytic fit/reverse "
                "bounds; it does not validate a spatial swept path or aisle-centerline crossing"
            )
        else:
            warnings.append(
                "design-vehicle data is parsed and available, but no vehicle-level check was requested; legacy "
                "maneuver envelopes remain proxies"
            )
        if vehicle_status == "active_failed":
            warnings.append("a requested vehicle-level check failed and is fail-closed for final layout validity")
    elif any(_requested_vehicle_checks(site).values()):
        warnings.append("vehicle-level validation was requested without a design vehicle and will fail closed")
    if site.site_features:
        warnings.append(
            f"hard site-feature scopes are {'enforced' if layout else 'available for enforcement'} for stall, aisle, "
            "and/or swept-path geometry as declared; "
            "advisory/draw-only features remain non-blocking, and passing-bay markers also feed Phase 5Q proxies"
        )
    if site.pedestrian_and_emergency:
        warnings.append(
            f"hard pedestrian, accessible, fire, and emergency route scopes are "
            f"{'enforced' if layout else 'available for enforcement'} as declared; advisory or "
            "future-priority routes are drawn/reported without becoming hard exclusions"
        )
    if site.stall.drive_over or site.stall.blocked_sides or site.stall.access_sides != ("front",):
        parsed_future_fields.append("parking.stall access behavior")
        warnings.append("stall access_sides, blocked_sides, and drive_over are parsed but not enforced yet")
    if site.aisle_classes:
        parsed_future_fields.append("aisles.classes")
    if site.aisle_selection_mode != "fixed":
        warnings.append("aisle selection modes beyond fixed are documented but not optimized yet")
    if site.constraints.get("maneuvering"):
        warnings.append(
            "maneuver requests are fail-closed for the supported perpendicular-90 template; unsupported stall "
            "families or missing required vehicle geometry cannot pass through a proxy fallback"
        )
    if site.optimization:
        parsed_future_fields.append("optimization")
        warnings.append("optimization weights are active for Phase 1 scoring; candidate generation is still deliberately narrow")
    if layout and layout.operational_quality:
        mode = layout.operational_quality.get("mode", "score_only")
        warnings.append(f"operational quality checks are active in {mode} mode for Phase 5Q")
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
        "vehicle_validation": _vehicle_validation_diagnostic(site, layout),
        "site_constraint_validation": layout.site_constraint_validation if layout else None,
        "engineering_validation": layout.engineering_validation if layout else None,
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
            "status": _constraint_source_status(site, layout, {"obstacle"}),
            "note": (
                "Hard polygon obstacles use the larger of their declared clearance and the project obstacle setback."
            ),
        },
        {
            "constraint": "declared site exclusions",
            "status": _site_constraint_status(layout),
            "note": (
                "Hard obstacle, reserved-area, feature, and route scopes are checked against the exact official "
                "stall/aisle layout; advisory authority or priority remains non-blocking."
            ),
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
                "Phase 3C-2 validates angled stall access envelopes for generated main-aisle and branch stalls."
                if _angled_maneuver_status(site, layout) == "active"
                else "Angled stall maneuver validation is available only when the active stall family is angled."
            ),
        },
        {
            "constraint": "operational quality risk report",
            "status": _operational_quality_status(layout),
            "note": (
                "Phase 5Q reports junction, entrance-throat, route, route-summary, directionality, narrow two-way exposure, passing bay geometry/spacing/entrance-junction/mid-aisle-junction meeting risks, and narrow two-way junction-merge risks; configured modes can score, gate promotion, or hard-reject risky layouts."
                if _operational_quality_status(layout) == "active"
                else "Operational quality checks are only available after layout generation."
            ),
        },
        {
            "constraint": "v0.3 engineering validation contract",
            "status": _engineering_validation_status(layout),
            "note": (
                "Combines vehicle, hard site, quota, authority, advisory, unsupported, and failed-rule evidence for "
                "the exact official layout into one versioned fail-closed decision."
            ),
        },
        {
            "constraint": "vehicle turning radius",
            "status": _vehicle_check_status(site, layout, "turning_radius"),
            "note": (
                "When requested, the declared radius is resolved to the rear-axle-center path. "
                "outer_front_wheel inputs require explicit wheelbase and track width and fail closed if conversion "
                "cannot be audited."
            ),
        },
        {
            "constraint": "vehicle swept path",
            "status": _vehicle_check_status(site, layout, "swept_path"),
            "note": (
                "Optional exact mode supports perpendicular-90 reverse-in only: exact constant-curvature bicycle "
                "poses are wrapped by a conservative sampled body envelope and checked against drivable, boundary, "
                "centerline, and hard-exclusion geometry."
            ),
        },
        {
            "constraint": "vehicle reverse distance",
            "status": _vehicle_check_status(site, layout, "reverse_distance"),
            "note": (
                "Exact mode measures its template path; conservative mode uses a fail-closed quarter-turn-plus-stall "
                "upper bound."
            ),
        },
        {
            "constraint": "narrow two-way deadlock",
            "status": "future",
            "note": "Narrow two-way aisles should remain disabled until graph deadlock checks exist.",
        },
        {
            "constraint": "pedestrian and emergency reservations",
            "status": _constraint_source_status(
                site,
                layout,
                {"pedestrian_route", "accessible_route", "fire_lane", "access_route", "emergency_access_route"},
            ),
            "note": (
                "Hard declared route scopes exclude stalls, aisles, and/or swept paths; advisory/future-priority "
                "routes stay non-blocking. Required route declarations without usable hard geometry fail closed."
            ),
        },
        {
            "constraint": "accessible and EV minimum quotas",
            "status": _quota_status(site, layout),
            "note": (
                "Positive minimums count final stalls by explicit stall-type classifications or charging features; "
                "an unmet or unsupported positive quota invalidates the layout."
            ),
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
        "site.obstacles.polygon": _constraint_source_status(site, layout, {"obstacle"}),
        "site.reserved_areas": _constraint_source_status(site, layout, {"reserved_area"}),
        "site_features": _constraint_source_status(site, layout, {"site_feature"}),
        "entrances": "active" if _phase1_main_aisle_active(layout) else ("drawn_not_enforced" if site.entrances else "future"),
        "pedestrian_and_emergency.pedestrian_routes": _pedestrian_status(site, "pedestrian_routes", layout),
        "pedestrian_and_emergency.accessible_routes": _pedestrian_status(site, "accessible_routes", layout),
        "pedestrian_and_emergency.fire_lanes": _pedestrian_status(site, "fire_lanes", layout),
        "pedestrian_and_emergency.access_routes": _pedestrian_status(site, "access_routes", layout),
        "vehicles.design_vehicle": _vehicle_field_status(site, layout),
        "parking.standard_perpendicular": "active",
        "parking.stall_type_classifications": _classification_status(site, layout),
        "parking.quotas.accessible_min": _quota_key_status(site, layout, "accessible_min"),
        "parking.quotas.ev_min": _quota_key_status(site, layout, "ev_min"),
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
        "constraints.operational_directionality_risk": _operational_quality_status(layout),
        "constraints.operational_narrow_two_way_risk": _operational_quality_status(layout),
        "constraints.site_hard_exclusions": _site_constraint_status(layout),
        "constraints.turning_radius": _vehicle_check_status(site, layout, "turning_radius"),
        "constraints.swept_path": _vehicle_check_status(site, layout, "swept_path"),
        "constraints.reverse_distance": _vehicle_check_status(site, layout, "reverse_distance"),
        "optimization.weights": "active" if layout and layout.score else ("available" if site.optimization else "future"),
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
        "optimization.operational_max_average_route_length": "available" if layout else "future",
        "optimization.operational_average_route_length_risk": "available" if layout else "future",
        "optimization.operational_max_long_route_ratio": "available" if layout else "future",
        "optimization.operational_long_route_ratio_risk": "available" if layout else "future",
        "optimization.operational_directionality_issue_risk": "available" if layout else "future",
        "optimization.operational_max_directionality_issue_ratio": "available" if layout else "future",
        "optimization.operational_directionality_issue_ratio_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_issue_risk": "available" if layout else "future",
        "optimization.operational_max_narrow_two_way_stall_ratio": "available" if layout else "future",
        "optimization.operational_narrow_two_way_stall_ratio_risk": "available" if layout else "future",
        "optimization.operational_min_passing_bays": "available" if layout else "future",
        "optimization.operational_passing_bay_shortage_risk": "available" if layout else "future",
        "optimization.operational_passing_bay_touch_tolerance": "available" if layout else "future",
        "optimization.operational_min_passing_bay_area": "available" if layout else "future",
        "optimization.operational_passing_bay_geometry_issue_risk": "available" if layout else "future",
        "optimization.operational_max_passing_bay_spacing": "available" if layout else "future",
        "optimization.operational_passing_bay_spacing_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_meeting_gap_risk": "available" if layout else "future",
        "optimization.operational_narrow_two_way_junction_merge_risk": "available" if layout else "future",
        "optimization.candidate_layout_preview": "active" if layout and layout.candidate_layout_preview else "future",
        "optimization.candidate_layout_preview_scoring": "active" if _layout_preview_comparison(layout) else "future",
        "optimization.promote_candidate_layout_preview": _candidate_layout_promotion_status(layout),
        "diagnostics.report": "active",
        "diagnostics.engineering_validation": _engineering_validation_status(layout),
        "diagnostics.svg_candidate_network_preview": "active" if layout and layout.candidate_network_preview else "future",
        "diagnostics.debug_layers": "active",
    }
    return support


def _pedestrian_status(site: SiteSpec, key: str, layout: LayoutResult | None = None) -> str:
    source_by_key = {
        "pedestrian_routes": "pedestrian_route",
        "accessible_routes": "accessible_route",
        "fire_lanes": "fire_lane",
        "access_routes": "access_route",
        "emergency_access_routes": "emergency_access_route",
    }
    source = source_by_key.get(key)
    if source is None:
        return "future"
    return _constraint_source_status(site, layout, {source})


def _declared_constraints(site: SiteSpec):
    try:
        return declared_constraint_geometries(site, include_advisory=True)
    except ValueError:
        return None


def _constraint_source_status(
    site: SiteSpec,
    layout: LayoutResult | None,
    sources: set[str],
) -> str:
    declared = _declared_constraints(site)
    if declared is None:
        if layout is not None and layout.site_constraint_validation:
            return "active_failed"
        return "declared_invalid"
    declarations = [item for item in declared if item.source in sources]
    if not declarations:
        return "available"
    if not any(item.hard for item in declarations):
        return "advisory_only"
    if layout is None or not layout.site_constraint_validation:
        return "declared_pending_layout"
    return "active" if layout.site_constraint_validation.get("valid", False) else "active_failed"


def _site_constraint_status(layout: LayoutResult | None) -> str:
    if layout is None or not layout.site_constraint_validation:
        return "available"
    return "active" if layout.site_constraint_validation.get("valid", False) else "active_failed"


def _maneuvering_settings(site: SiteSpec) -> dict[str, Any]:
    raw = site.constraints.get("maneuvering", {})
    return raw if isinstance(raw, dict) else {}


def _boolean_setting(raw: object) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() not in {"", "false", "0", "no", "off"}
    return bool(raw)


def _declared_vehicle_requests(site: SiteSpec) -> dict[str, bool]:
    maneuvering = _maneuvering_settings(site)
    swept_path = _boolean_setting(maneuvering.get("require_swept_path_check", False))
    turning_radius = _boolean_setting(maneuvering.get("require_turning_radius_check", False))
    reverse_distance = (
        maneuvering.get("max_reverse_distance") is not None
        or bool(site.vehicle and site.vehicle.max_reverse_distance is not None)
    )
    return {
        "turning_radius": turning_radius,
        "swept_path": swept_path,
        "reverse_distance": reverse_distance,
    }


def _requested_vehicle_checks(site: SiteSpec) -> dict[str, bool]:
    declared = _declared_vehicle_requests(site)
    return {
        "turning_radius": (
            declared["turning_radius"] or declared["swept_path"] or declared["reverse_distance"]
        ),
        "swept_path": declared["swept_path"],
        "reverse_distance": declared["reverse_distance"],
    }


def _vehicle_validation_report(layout: LayoutResult | None) -> dict[str, Any]:
    if layout is None or not isinstance(layout.maneuver_validation, dict):
        return {}
    report = layout.maneuver_validation.get("vehicle_validation", {})
    return report if isinstance(report, dict) else {}


def _vehicle_validation_mode(site: SiteSpec, layout: LayoutResult | None) -> str:
    requested = _requested_vehicle_checks(site)
    if requested["swept_path"]:
        return "exact_swept_path"
    if requested["turning_radius"] or requested["reverse_distance"]:
        return "conservative_analytic"
    return "not_requested"


def _vehicle_field_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    requested = _requested_vehicle_checks(site)
    any_requested = any(requested.values())
    if site.vehicle is None:
        return "requested_missing" if any_requested else "available"
    if not any_requested:
        return "parsed_available"
    if layout is None:
        return "requested_pending_layout"
    report = _vehicle_validation_report(layout)
    if not report or not report.get("valid", False):
        return "active_failed"
    return "active_exact" if requested["swept_path"] else "active_conservative"


def _vehicle_check_status(site: SiteSpec, layout: LayoutResult | None, check: str) -> str:
    requested = _requested_vehicle_checks(site)
    if not requested.get(check, False):
        return "available"
    if layout is None:
        return "requested_pending_layout"
    report = _vehicle_validation_report(layout)
    if not report or not report.get("valid", False):
        return "active_failed"
    return "active_exact" if requested["swept_path"] else "active_conservative"


def _vehicle_validation_diagnostic(site: SiteSpec, layout: LayoutResult | None) -> dict[str, Any]:
    report = _vehicle_validation_report(layout)
    requested = _requested_vehicle_checks(site)
    return {
        "mode": _vehicle_validation_mode(site, layout),
        "status": _vehicle_field_status(site, layout),
        "requested": requested,
        "declared_requests": _declared_vehicle_requests(site),
        "fail_closed_when_requested": True,
        "checked_stalls": int(report.get("checked_stalls", 0)),
        "invalid_stall_count": int(report.get("invalid_stall_count", 0)),
        "report_version": report.get("version"),
        "scope": (
            "perpendicular-90 reverse-in template with exact constant-curvature pose integration and a "
            "conservative sampled body envelope"
            if requested["swept_path"]
            else "rear-axle radius conversion, vehicle/stall fit, and conservative reverse-distance bound"
        ),
    }


def _quota_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not any(value > 0 for value in site.parking_quotas.values()):
        return "available"
    if layout is None or not layout.site_constraint_validation:
        return "declared_pending_layout"
    quota = layout.site_constraint_validation.get("quota", {})
    return "active" if isinstance(quota, dict) and quota.get("valid", False) else "active_failed"


def _quota_key_status(site: SiteSpec, layout: LayoutResult | None, key: str) -> str:
    if site.parking_quotas.get(key, 0) <= 0:
        return "available"
    return _quota_status(site, layout)


def _classification_status(site: SiteSpec, layout: LayoutResult | None) -> str:
    if not any(stall.classifications or stall.fixed_features for stall in _stall_candidates(site)):
        return "available"
    return "active" if layout else "declared_pending_layout"


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


def _engineering_validation_status(layout: LayoutResult | None) -> str:
    if not layout or not layout.engineering_validation:
        return "future"
    return "active" if layout.engineering_validation.get("valid", False) else "active_failed"


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
