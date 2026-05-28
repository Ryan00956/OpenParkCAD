from __future__ import annotations

from openparkcad.maneuver_validation import apply_maneuver_filter
from openparkcad.models import AngleAttempt, LayoutResult, SiteSpec
from openparkcad.phase1_candidates import iter_phase1_candidates
from openparkcad.phase1_support import phase1_unsupported_inputs
from openparkcad.scoring import score_layout, score_total
from openparkcad.traffic_graph import build_traffic_graph, validate_traffic_graph


def generate_layout(site: SiteSpec) -> LayoutResult:
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
        graph_validation=best.graph_validation,
        maneuver_validation=best.maneuver_validation,
        unsupported_phase1_inputs=unsupported,
    )
    return _with_score(result)


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
