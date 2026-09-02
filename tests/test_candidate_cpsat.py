"""Optional CP-SAT shadow selector. Success tests skip without OR-Tools."""

from dataclasses import replace

import pytest

from openparkcad.candidate_catalog import SELECTOR_VERSION
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, CandidateObject, EntranceSpec, SiteSpec, StallSpec


def _loop_objects() -> list[CandidateObject]:
    return [
        CandidateObject(
            id="C-BRANCH-1",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(0, 0), (6, 0), (6, 20), (0, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-BRANCH-2",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(10, 0), (16, 0), (16, 20), (10, 20)],
            score_features={"stall_count": 20.0, "base_stall_count": 10.0},
            metadata={"source_id": "A-BRANCH-002"},
        ),
        CandidateObject(
            id="C-CONNECTOR-1",
            kind="aisle_skeleton",
            role="connector",
            status="rejected",
            geometry=[(0, 18), (16, 18), (16, 24), (0, 24)],
            score_features={"added_stalls": 1.0, "removed_stalls": 0.0},
            metadata={"source_id": "A-CONNECTOR-001", "connects": ["A-BRANCH-001", "A-BRANCH-002"]},
        ),
    ]


def _site(**optimization) -> SiteSpec:
    return SiteSpec(
        name="cpsat-selector",
        boundary=[(0, 0), (40, 0), (40, 40), (0, 40)],
        optimization={"max_branches": 2, **optimization},
    )


def test_cpsat_request_falls_back_when_ortools_missing(monkeypatch):
    import openparkcad.candidate_cpsat as cpsat

    def _missing():
        raise ImportError("simulated missing optimizer extra")

    monkeypatch.setattr(cpsat, "import_cp_model", _missing)
    selection = select_candidate_objects(_loop_objects(), _site(selector_backend="cpsat"))
    assert selection["version"] == SELECTOR_VERSION
    assert selection["requested_backend"] == "cpsat"
    assert selection["backend"] == "greedy"
    assert selection["backend_fallback_reason"] == "cpsat_backend_unavailable"
    assert selection["solver_provenance"]["backend"] == "greedy"
    assert set(selection["selected_ids"]) == {"C-BRANCH-1", "C-BRANCH-2", "C-CONNECTOR-1"}


def test_unknown_selector_backend_stays_greedy_without_calling_cpsat(monkeypatch):
    import openparkcad.candidate_cpsat as cpsat

    def _should_not_run():
        raise AssertionError("CP-SAT must not run for unknown backends")

    monkeypatch.setattr(cpsat, "import_cp_model", _should_not_run)
    selection = select_candidate_objects(_loop_objects(), _site(selector_backend="mystery"))
    assert selection["backend"] == "greedy"
    assert selection["requested_backend"] == "greedy"
    assert selection["backend_fallback_reason"] == "unknown_selector_backend"


def test_default_greedy_path_does_not_import_ortools(monkeypatch):
    import openparkcad.candidate_cpsat as cpsat

    def _should_not_run():
        raise AssertionError("default greedy must not import OR-Tools")

    monkeypatch.setattr(cpsat, "import_cp_model", _should_not_run)
    selection = select_candidate_objects(_loop_objects(), _site())
    assert selection["backend"] == "greedy"
    assert selection["requested_backend"] == "greedy"
    assert selection["backend_fallback_reason"] is None


def test_cpsat_selects_loop_bundle_and_respects_connector_dependency():
    pytest.importorskip("ortools")
    selection = select_candidate_objects(
        _loop_objects(),
        _site(selector_backend="cpsat", selector_seed=1, selector_time_limit_seconds=2),
    )
    assert selection["backend"] == "cpsat"
    assert selection["requested_backend"] == "cpsat"
    assert selection["backend_fallback_reason"] is None
    assert selection["strategy"] == "cpsat_aisle_skeleton_selector"
    assert set(selection["selected_ids"]) == {"C-BRANCH-1", "C-BRANCH-2", "C-CONNECTOR-1"}
    assert selection["selected_branch_count"] == 2
    assert selection["selected_connector_count"] == 1
    assert selection["selected_bundle_count"] == 1
    assert selection["solver_provenance"]["status"] in {"optimal", "feasible"}
    assert selection["solver_provenance"]["seed"] == 1
    assert selection["solver_provenance"]["time_limit_seconds"] == 2.0

    incomplete = _loop_objects()[:1] + [_loop_objects()[2]]
    blocked = select_candidate_objects(incomplete, _site(selector_backend="cpsat"))
    assert blocked["backend"] == "cpsat"
    assert "C-CONNECTOR-1" not in blocked["selected_ids"]
    assert any(item["reason"] == "connector_dependency_not_selected" for item in blocked["rejected"])


def test_cpsat_respects_max_branches_and_source_uniqueness():
    pytest.importorskip("ortools")
    objects = [
        CandidateObject(
            id="C-BRANCH-A1",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(0, 0), (6, 0), (6, 12), (0, 12)],
            score_features={"stall_count": 8.0, "base_stall_count": 0.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-BRANCH-A2",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(1, 0), (7, 0), (7, 10), (1, 10)],
            score_features={"stall_count": 4.0, "base_stall_count": 0.0},
            metadata={"source_id": "A-BRANCH-001"},
        ),
        CandidateObject(
            id="C-BRANCH-B",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(20, 0), (26, 0), (26, 12), (20, 12)],
            score_features={"stall_count": 7.0, "base_stall_count": 0.0},
            metadata={"source_id": "A-BRANCH-002"},
        ),
        CandidateObject(
            id="C-BRANCH-C",
            kind="aisle_skeleton",
            role="branch",
            status="rejected",
            geometry=[(30, 0), (36, 0), (36, 12), (30, 12)],
            score_features={"stall_count": 6.0, "base_stall_count": 0.0},
            metadata={"source_id": "A-BRANCH-003"},
        ),
    ]
    selection = select_candidate_objects(objects, _site(selector_backend="cpsat", max_branches=2))
    assert selection["backend"] == "cpsat"
    assert selection["selected_branch_count"] == 2
    sources = selection["selected_branch_source_ids"]
    assert len(sources) == 2
    assert "A-BRANCH-001" in sources
    selected = set(selection["selected_ids"])
    assert "C-BRANCH-A1" in selected
    assert "C-BRANCH-A2" not in selected


def test_generate_layout_cpsat_is_shadow_only():
    pytest.importorskip("ortools")
    site = SiteSpec(
        name="cpsat-layout",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="wide-two-way-no-cross",
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "enable_t_end_caps": False,
            "selector_backend": "cpsat",
        },
    )
    greedy_site = replace(site, optimization={**site.optimization, "selector_backend": "greedy"})
    cpsat_layout = generate_layout(site)
    greedy_layout = generate_layout(greedy_site)
    assert cpsat_layout.candidate_selection["backend"] == "cpsat"
    assert greedy_layout.candidate_selection["backend"] == "greedy"
    assert [aisle.id for aisle in cpsat_layout.aisles] == [aisle.id for aisle in greedy_layout.aisles]
    assert [stall.id for stall in cpsat_layout.stalls] == [stall.id for stall in greedy_layout.stalls]
    diagnostics = build_input_diagnostics(cpsat_layout.site, cpsat_layout)
    assert diagnostics["field_support"]["optimization.selector_backend"] == "active"
    assert diagnostics["field_support"]["optimization.selector_backend_cpsat"] == "active"
