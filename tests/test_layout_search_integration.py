from __future__ import annotations

from dataclasses import replace

from openparkcad.cli import _final_layout_errors
from openparkcad.generator import generate_layout, generate_layout_legacy
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.scoring import score_total


def _aisle_class() -> AisleClassSpec:
    return AisleClassSpec(
        id="wide-two-way-no-cross",
        width=6.0,
        capacity="two_vehicle",
        directionality="two_way",
    )


def _simple_site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="search-integration",
        boundary=[(0, 0), (36, 0), (36, 40), (0, 40)],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[EntranceSpec(id="main", mode="shared", center=(18, 0), width=8.0, heading_degrees=90.0)],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_branches": False,
            "enable_connectors": False,
            "enable_t_end_caps": False,
            "main_aisle_lateral_offsets": [0.0, 6.0],
            **optimization,
        },
    )


def _geometry_key(layout) -> tuple:
    return (
        layout.stall_count,
        tuple(stall.id for stall in layout.stalls),
        tuple((aisle.id, aisle.role, tuple(aisle.polygon)) for aisle in layout.aisles),
        round(score_total(layout), 6),
        bool(_final_layout_errors(layout)),
    )


def test_default_generate_layout_matches_legacy_wrapper() -> None:
    site = _simple_site()
    default = generate_layout(site)
    legacy = generate_layout_legacy(site)
    assert _geometry_key(default) == _geometry_key(legacy)
    assert not default.layout_search or default.layout_search.get("mode") in {None, "legacy"}


def test_explicit_legacy_mode_matches_wrapper() -> None:
    site = _simple_site(layout_search={"mode": "legacy", "top_k": 4})
    assert _geometry_key(generate_layout(site)) == _geometry_key(generate_layout_legacy(site))


def test_top_k_one_reuses_legacy_official_geometry() -> None:
    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 1, "refinement_budget_seconds": 10.0},
        promote_candidate_layout_preview=True,
    )
    legacy = generate_layout_legacy(site)
    multi = generate_layout(site)
    assert _geometry_key(legacy) == _geometry_key(multi)
    assert multi.layout_search["mode"] == "multi_spine"
    assert multi.layout_search["publication"]["reason"] == "top_k_reuses_legacy"


def test_promotion_off_keeps_baseline_official_object() -> None:
    site = _simple_site(
        layout_search={"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 30.0},
        promote_candidate_layout_preview=False,
    )
    legacy = generate_layout_legacy(replace(site, optimization={**site.optimization, "layout_search": {"mode": "legacy"}}))
    multi = generate_layout(site)
    assert _geometry_key(legacy) == _geometry_key(multi)
    assert multi.layout_search["publication"]["replaced"] is False
    assert multi.layout_search["publication"]["reason"] == "promotion_not_requested"
    assert multi.layout_search["candidates"]


def _t04_site(offsets: list[float], **extra) -> SiteSpec:
    return SiteSpec(
        name="multi-spine-comparison",
        boundary=[(0, 0), (48, 0), (48, 52), (0, 52)],
        obstacles=[[(18, 10), (30, 10), (30, 28), (18, 28)]],
        stall=StallSpec(id="standard-90", width=2.5, length=5.0, family="perpendicular", allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[EntranceSpec(id="main", mode="shared", center=(24, 0), width=8.0, heading_degrees=90.0)],
        aisle_classes=[_aisle_class()],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_connectors": False,
            "enable_branches": True,
            "max_branches": 2,
            "branch_sides": ["left"],
            "enable_t_end_caps": False,
            "prefer_obstacle_clearance": True,
            "enable_main_aisle_dogleg": True,
            "auto_lateral_offsets_for_obstacles": False,
            "enable_adaptive_dogleg_offsets": False,
            "dogleg_offsets": offsets,
            "promote_candidate_layout_preview": True,
            "selector_backend": "greedy",
            "layout_search": {"mode": "multi_spine", "top_k": 4, "refinement_budget_seconds": 30.0},
            **extra,
        },
    )


def test_t04_second_template_spine_wins_after_official_rebuild() -> None:
    from openparkcad.generator import collect_layout_candidate_contexts, generate_layout_legacy
    from openparkcad.layout_evaluation import evaluate_layout_candidate
    from openparkcad.layout_search import search_multi_spine

    full = _t04_site([-12.9, -9.9])
    weak = _t04_site([-9.9])
    baseline = generate_layout_legacy(weak)
    assert baseline.stall_count > 0
    assert not _final_layout_errors(baseline)

    result = search_multi_spine(
        full,
        collect_fn=collect_layout_candidate_contexts,
        evaluate_fn=evaluate_layout_candidate,
        legacy_fn=lambda _site: baseline,
    )
    assert result.layout_search["publication"]["replaced"] is True
    assert result.layout_search["publication"]["reason"] == "higher_scoring_verified_candidate"
    assert _geometry_key(result) != _geometry_key(baseline)
    assert score_total(result) > score_total(baseline)
    assert not _final_layout_errors(result)
    checks = result.graph_validation, result.maneuver_validation, result.site_constraint_validation
    assert all(block.get("valid") is True for block in checks)
    assert result.engineering_validation.get("result_scope") == "official_layout"
    assert result.engineering_validation.get("valid") is True
    assert result.operational_quality.get("valid") is True
    assert result.graph_validation.get("errors") == [] or result.graph_validation.get("executed", True)
    assert result.aisles and result.stalls
    official_ids = {aisle.id for aisle in result.aisles}
    assert all(stall.served_by_aisle_id in official_ids or stall.served_by_aisle_id is None for stall in result.stalls)


def test_t05_unverified_or_invalid_candidate_cannot_replace_baseline() -> None:
    from openparkcad.layout_candidates import LayoutCandidateEvaluation
    from openparkcad.layout_search import choose_official
    from openparkcad.models import LayoutResult

    baseline = generate_layout(_simple_site())
    shadow = LayoutResult(
        site=baseline.site,
        stalls=list(baseline.stalls) + list(baseline.stalls[:1]),
        aisles=list(baseline.aisles),
        generation_mode=baseline.generation_mode,
        main_entrance_id=baseline.main_entrance_id,
        score={"total": score_total(baseline) + 5000},
        graph_validation={"valid": False, "errors": ["shadow-only"]},
        maneuver_validation={"valid": True},
        site_constraint_validation={"valid": True},
        engineering_validation={"valid": False, "result_scope": "candidate:shadow"},
        operational_quality={"valid": True},
    )
    evaluation = LayoutCandidateEvaluation(
        candidate_id="cand-shadow",
        spine_id="spine-shadow",
        requested_backend="greedy",
        actual_backend="greedy",
        fallback_reason=None,
        preview={"comparison": {"promotion_eligible": True}},
        rebuilt_layout=shadow,
        checks={
            "graph": {"executed": True, "valid": False},
            "maneuver": {"executed": True, "valid": True},
            "vehicle": {"executed": True, "valid": True},
            "site_quota": {"executed": True, "valid": True},
            "engineering": {"executed": True, "valid": False},
            "operational": {"executed": True, "valid": True},
        },
        score={"total": score_total(baseline) + 5000},
        duration_seconds=0.1,
        failure_class=None,
        used_template=False,
    )
    official, publication, _ = choose_official(baseline, [evaluation], promotion_requested=True)
    assert official is baseline
    assert publication["replaced"] is False
