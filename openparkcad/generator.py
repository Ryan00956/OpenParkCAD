from __future__ import annotations

from dataclasses import replace

from openparkcad.maneuver_validation import apply_maneuver_filter
from openparkcad.models import AngleAttempt, LayoutResult, SiteSpec, StallSpec
from openparkcad.phase1_candidates import iter_phase1_candidates
from openparkcad.phase1_support import phase1_unsupported_inputs
from openparkcad.scoring import score_layout, score_total
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def generate_layout(site: SiteSpec) -> LayoutResult:
    candidates = _candidate_stalls(site)
    if len(candidates) <= 1:
        selected_site = _site_with_stall(site, candidates[0])
        layout = _generate_layout_for_site(selected_site)
        object.__setattr__(layout, "site", replace(selected_site, stall_candidates=candidates))
        object.__setattr__(layout, "selected_stall_type_id", candidates[0].id)
        object.__setattr__(layout, "stall_type_attempts", [_stall_type_attempt(layout, selected=True)])
        return layout

    layouts = [_generate_layout_for_site(_site_with_stall(site, stall)) for stall in candidates]
    valid_layouts = [layout for layout in layouts if _graph_valid(layout)]
    best = max(valid_layouts or layouts, key=score_total)
    selected_site = replace(site, stall=best.site.stall, angle_degrees=best.site.stall.allowed_angles[0], stall_candidates=candidates)
    object.__setattr__(best, "site", selected_site)
    object.__setattr__(best, "selected_stall_type_id", best.site.stall.id)
    object.__setattr__(
        best,
        "stall_type_attempts",
        [_stall_type_attempt(layout, selected=layout.site.stall.id == best.site.stall.id) for layout in layouts],
    )
    object.__setattr__(best, "unsupported_phase1_inputs", phase1_unsupported_inputs(selected_site))
    return best


def _generate_layout_for_site(site: SiteSpec) -> LayoutResult:
    """Generate the current Phase 1 layout.

    Phase 1 deliberately supports one conservative pattern:

    entrance -> straight wide two-way main aisle -> end turnaround -> stalls on both sides
    """
    attempts: list[AngleAttempt] = []
    best: LayoutResult | None = None
    unsupported = phase1_unsupported_inputs(site)

    for candidate in iter_phase1_candidates(site, _finalize_candidate, _graph_valid, score_total):
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
        if _graph_valid(layout) and (best is None or score_total(layout) > score_total(best)):
            best = layout

    if best is None:
        return _with_score(
            _with_graph_validation(
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
        )

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
        unsupported_phase1_inputs=unsupported,
    )
    return _with_score(result)


def _candidate_stalls(site: SiteSpec) -> tuple[StallSpec, ...]:
    return site.stall_candidates or (site.stall,)


def _site_with_stall(site: SiteSpec, stall: StallSpec) -> SiteSpec:
    return replace(
        site,
        stall=stall,
        angle_degrees=stall.allowed_angles[0],
        stall_candidates=(),
    )


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
        "unsupported_phase1_inputs": list(layout.unsupported_phase1_inputs),
        "selected": selected,
    }


def _with_score(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "score", score_layout(layout))
    return layout


def _with_graph_validation(layout: LayoutResult) -> LayoutResult:
    object.__setattr__(layout, "graph_validation", validate_traffic_graph(build_traffic_graph(layout), layout))
    return layout


def _finalize_candidate(layout: LayoutResult) -> LayoutResult:
    return _with_score(_with_graph_validation(apply_maneuver_filter(layout)))


def _graph_valid(layout: LayoutResult) -> bool:
    return bool(layout.graph_validation.get("valid", False))
