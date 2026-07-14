from __future__ import annotations

from dataclasses import replace
from itertools import product

from openparkcad.candidate_snapshot import attach_candidate_snapshot
from openparkcad.maneuver_validation import apply_maneuver_filter
from openparkcad.models import AngleAttempt, LayoutResult, SiteSpec, StallSpec
from openparkcad.operational_quality import operational_quality_report
from openparkcad.phase1_candidates import iter_phase1_candidates
from openparkcad.phase1_support import phase1_unsupported_inputs
from openparkcad.scoring import score_layout, score_total
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def generate_layout(site: SiteSpec) -> LayoutResult:
    candidates = _candidate_stalls(site)
    assignments = _candidate_stall_assignments(site)
    if len(assignments) <= 1:
        selected_site = _site_with_stall_assignment(site, candidates[0], candidates[0])
        layout = _generate_layout_for_site(selected_site)
        selected = _layout_valid(layout)
        object.__setattr__(layout, "site", replace(selected_site, stall_candidates=candidates))
        object.__setattr__(layout, "selected_stall_type_id", candidates[0].id if selected else None)
        object.__setattr__(
            layout,
            "selected_stall_assignment",
            _assignment_dict(candidates[0], candidates[0]) if selected else {},
        )
        object.__setattr__(layout, "stall_type_attempts", [_stall_type_attempt(layout, selected=selected)])
        object.__setattr__(layout, "stall_assignment_attempts", [_stall_assignment_attempt(layout, selected=selected)])
        return attach_candidate_snapshot(layout)

    layouts = [
        _generate_layout_for_site(_site_with_stall_assignment(site, main_stall, branch_stall))
        for main_stall, branch_stall in assignments
    ]
    valid_layouts = [layout for layout in layouts if _layout_valid(layout)]
    best = max(valid_layouts or layouts, key=score_total)
    best_is_valid = _layout_valid(best)
    selected_main = best.site.main_stall or best.site.stall
    selected_branch = best.site.branch_stall or selected_main
    selected_site = replace(
        site,
        stall=selected_main,
        main_stall=selected_main,
        branch_stall=selected_branch,
        angle_degrees=selected_main.allowed_angles[0],
        stall_candidates=candidates,
    )
    object.__setattr__(best, "site", selected_site)
    object.__setattr__(
        best,
        "selected_stall_type_id",
        selected_main.id if best_is_valid and selected_main.id == selected_branch.id else None,
    )
    object.__setattr__(
        best,
        "selected_stall_assignment",
        _assignment_dict(selected_main, selected_branch) if best_is_valid else {},
    )
    object.__setattr__(
        best,
        "stall_type_attempts",
        [
            _stall_type_attempt(
                layout,
                selected=best_is_valid
                and (layout.site.main_stall or layout.site.stall).id == selected_main.id
                and (layout.site.branch_stall or layout.site.stall).id == selected_branch.id,
            )
            for layout in layouts
            if (layout.site.main_stall or layout.site.stall).id == (layout.site.branch_stall or layout.site.stall).id
        ],
    )
    object.__setattr__(
        best,
        "stall_assignment_attempts",
        [
            _stall_assignment_attempt(
                layout,
                selected=best_is_valid
                and _assignment_dict(layout.site.main_stall or layout.site.stall, layout.site.branch_stall or layout.site.stall)
                == _assignment_dict(selected_main, selected_branch),
            )
            for layout in layouts
        ],
    )
    object.__setattr__(best, "unsupported_phase1_inputs", phase1_unsupported_inputs(selected_site))
    return attach_candidate_snapshot(best)


def _generate_layout_for_site(site: SiteSpec) -> LayoutResult:
    """Generate the current Phase 1 layout.

    Phase 1 deliberately supports one conservative pattern:

    entrance -> straight wide two-way main aisle -> end turnaround -> stalls on both sides
    """
    attempts: list[AngleAttempt] = []
    best: LayoutResult | None = None
    best_structural_rejection: LayoutResult | None = None
    best_operational_rejection: LayoutResult | None = None
    unsupported = phase1_unsupported_inputs(site)

    for candidate in iter_phase1_candidates(site, _finalize_candidate, _layout_valid, score_total):
        layout = candidate.layout
        attempts.append(
            AngleAttempt(
                angle_degrees=candidate.heading_degrees,
                stall_count=layout.stall_count,
                entrance_id=candidate.entrance_id,
                heading_delta_degrees=candidate.heading_delta_degrees,
                entrance_offset=candidate.entrance_offset,
                branch_side=layout.selected_branch_side,
                branch_start_u=layout.selected_branch_start_u,
                branch_length=layout.selected_branch_length,
                branch_candidates=candidate.branch_candidates,
                graph_valid=_graph_valid(layout),
                graph_errors=list(layout.graph_validation.get("errors", [])),
            )
        )
        if (
            _graph_valid(layout)
            and _maneuver_valid(layout)
            and _operational_valid(layout)
            and not _has_minimum_layout_content(layout)
            and (
                best_structural_rejection is None
                or score_total(layout) > score_total(best_structural_rejection)
            )
        ):
            best_structural_rejection = layout
        if (
            _graph_valid(layout)
            and _maneuver_valid(layout)
            and not _operational_valid(layout)
            and (
                best_operational_rejection is None
                or score_total(layout) > score_total(best_operational_rejection)
            )
        ):
            best_operational_rejection = layout
        if _layout_valid(layout) and (best is None or score_total(layout) > score_total(best)):
            best = layout

    if best is None:
        empty = _with_graph_validation(
            apply_maneuver_filter(
                LayoutResult(
                    site=site,
                    stalls=[],
                    generation_mode="phase1_main_aisle",
                    attempts=attempts,
                    unsupported_phase1_inputs=unsupported,
                )
            )
        )
        if best_structural_rejection is not None:
            object.__setattr__(empty, "maneuver_validation", dict(best_structural_rejection.maneuver_validation))
        if best_operational_rejection is None:
            empty = _with_operational_quality(empty)
        else:
            report = dict(best_operational_rejection.operational_quality)
            report["result_scope"] = "best_rejected_candidate"
            report["rejected_candidate_stall_count"] = best_operational_rejection.stall_count
            object.__setattr__(empty, "operational_quality", report)
        return _with_score(empty)

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
        selected_branches=list(best.selected_branches),
        selected_connectors=list(best.selected_connectors),
        selected_stall_type_id=best.site.stall.id,
        graph_validation=best.graph_validation,
        maneuver_validation=best.maneuver_validation,
        operational_quality=best.operational_quality,
        unsupported_phase1_inputs=unsupported,
    )
    return _with_score(result)


def _candidate_stalls(site: SiteSpec) -> tuple[StallSpec, ...]:
    return site.stall_candidates or (site.stall,)


def _candidate_stall_assignments(site: SiteSpec) -> tuple[tuple[StallSpec, StallSpec], ...]:
    candidates = _candidate_stalls(site)
    if len(candidates) <= 1:
        return ((candidates[0], candidates[0]),)
    return tuple(product(candidates, candidates))


def _site_with_stall_assignment(site: SiteSpec, main_stall: StallSpec, branch_stall: StallSpec) -> SiteSpec:
    return replace(
        site,
        stall=main_stall,
        main_stall=main_stall,
        branch_stall=branch_stall,
        angle_degrees=main_stall.allowed_angles[0],
        stall_candidates=(),
    )


def _assignment_dict(main_stall: StallSpec, branch_stall: StallSpec) -> dict[str, str]:
    return {
        "main": main_stall.id,
        "branch": branch_stall.id,
        "connector": branch_stall.id,
    }


def _stall_type_attempt(layout: LayoutResult, selected: bool) -> dict[str, object]:
    return {
        "id": layout.site.stall.id,
        "family": layout.site.stall.family,
        "allowed_angles": list(layout.site.stall.allowed_angles),
        "stall_count": layout.stall_count,
        "score_total": score_total(layout),
        "graph_valid": _graph_valid(layout),
        "graph_errors": list(layout.graph_validation.get("errors", [])),
        "maneuver_valid": bool(layout.maneuver_validation.get("valid", False)),
        "maneuver_invalid_count": len(layout.maneuver_validation.get("invalid_stalls", [])),
        "operational_valid": _operational_valid(layout),
        "operational_blockers": list(layout.operational_quality.get("promotion_blockers", [])),
        "layout_valid": _layout_valid(layout),
        "unsupported_phase1_inputs": list(layout.unsupported_phase1_inputs),
        "selected": selected,
    }


def _stall_assignment_attempt(layout: LayoutResult, selected: bool) -> dict[str, object]:
    main_stall = layout.site.main_stall or layout.site.stall
    branch_stall = layout.site.branch_stall or main_stall
    return {
        "main_stall_type_id": main_stall.id,
        "branch_stall_type_id": branch_stall.id,
        "connector_stall_type_id": branch_stall.id,
        "stall_type_counts": _stall_type_counts(layout),
        "stall_count": layout.stall_count,
        "score_total": score_total(layout),
        "graph_valid": _graph_valid(layout),
        "graph_errors": list(layout.graph_validation.get("errors", [])),
        "maneuver_valid": bool(layout.maneuver_validation.get("valid", False)),
        "maneuver_invalid_count": len(layout.maneuver_validation.get("invalid_stalls", [])),
        "operational_valid": _operational_valid(layout),
        "operational_blockers": list(layout.operational_quality.get("promotion_blockers", [])),
        "layout_valid": _layout_valid(layout),
        "unsupported_phase1_inputs": list(layout.unsupported_phase1_inputs),
        "selected": selected,
    }


def _stall_type_counts(layout: LayoutResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for stall in layout.stalls:
        stall_type_id = stall.stall_type_id or layout.site.stall.id
        counts[stall_type_id] = counts.get(stall_type_id, 0) + 1
    return counts


def _with_score(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "score", score_layout(layout))
    return layout


def _with_graph_validation(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "graph_validation", validate_traffic_graph(build_traffic_graph(layout), layout))
    return layout


def _with_operational_quality(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "operational_quality", operational_quality_report(layout))
    return layout


def _finalize_candidate(layout: LayoutResult) -> LayoutResult:
    return _with_score(_with_operational_quality(_with_graph_validation(apply_maneuver_filter(layout))))


def _graph_valid(layout: LayoutResult) -> bool:
    return bool(layout.graph_validation.get("valid", False))


def _layout_valid(layout: LayoutResult) -> bool:
    return (
        _has_minimum_layout_content(layout)
        and _graph_valid(layout)
        and _maneuver_valid(layout)
        and _operational_valid(layout)
    )


def _has_minimum_layout_content(layout: LayoutResult) -> bool:
    return layout.stall_count > 0 and bool(layout.aisles)


def _maneuver_valid(layout: LayoutResult) -> bool:
    return bool(layout.maneuver_validation.get("valid", False))


def _operational_valid(layout: LayoutResult) -> bool:
    return bool(layout.operational_quality.get("valid", False))
