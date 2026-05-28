from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad import diagnostics
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.phase1_support import phase1_unsupported_inputs


def _phase1_site() -> SiteSpec:
    return SiteSpec(
        name="phase1",
        boundary=[(0, 0), (24, 0), (24, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(12, 0),
                width=7.0,
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
        source_format="phase0",
    )


def test_phase1_layout_uses_entrance_connected_main_aisle():
    layout = generate_layout(_phase1_site())

    assert layout.generation_mode == "phase1_main_aisle"
    assert layout.main_entrance_id == "main"
    assert [aisle.id for aisle in layout.aisles] == ["A-MAIN", "A-TURNAROUND"]
    assert layout.aisles[0].role == "main"
    assert layout.aisles[0].connected_to_entrance_id == "main"
    assert layout.aisles[1].parent_aisle_id == "A-MAIN"
    assert layout.stall_count > 0
    assert all(stall.angle_degrees == 90.0 for stall in layout.stalls)
    assert {stall.served_by_aisle_id for stall in layout.stalls} == {"A-MAIN"}
    assert {stall.aisle_side for stall in layout.stalls} <= {"left", "right"}


def test_phase1_layout_keeps_generated_geometry_inside_usable_area():
    site = _phase1_site()
    layout = generate_layout(site)
    usable = ShapelyPolygon(site.boundary)

    for aisle in layout.aisles:
        assert usable.covers(ShapelyPolygon(aisle.polygon))
    for stall in layout.stalls:
        assert usable.covers(ShapelyPolygon(stall.polygon))


def test_phase1_layout_requires_supported_wide_two_way_aisle():
    site = _phase1_site()
    unsupported = SiteSpec(
        name=site.name,
        boundary=site.boundary,
        stall=site.stall,
        aisle_width=site.aisle_width,
        margin=site.margin,
        entrances=site.entrances,
        aisle_classes=[
            AisleClassSpec(
                id="narrow-one-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="one_way",
            )
        ],
        fixed_aisle_class="narrow-one-way",
        source_format="phase0",
    )

    layout = generate_layout(unsupported)

    assert layout.generation_mode == "phase1_main_aisle"
    assert layout.stall_count == 0
    assert layout.aisles == []
    assert layout.unsupported_phase1_inputs
    assert layout.unsupported_phase1_inputs[0]["field"] == "aisles.classes.narrow-one-way"


def test_phase2b_rejects_candidates_without_exit_path():
    site = SiteSpec(
        name="entry-only",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry",
                mode="entry_only",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0]},
    )

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.attempts
    assert layout.attempts[0].graph_valid is False
    assert layout.attempts[0].graph_errors == ["stalls_without_exit_path"]


def test_phase1_reports_unsupported_stall_types_clearly():
    site = SiteSpec(
        name="parallel",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=6.0, family="parallel", allowed_angles=(0.0,), access_sides=("left", "right")),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
    )

    layout = generate_layout(site)
    diagnostics = build_input_diagnostics(site, layout)

    assert layout.stall_count == 0
    assert {item["field"] for item in layout.unsupported_phase1_inputs} == {
        "parking.active_stall.family",
        "parking.active_stall.allowed_angles",
        "parking.active_stall.access_sides",
    }
    assert diagnostics["unsupported_phase1_inputs"] == layout.unsupported_phase1_inputs


def test_phase1_layout_evaluates_heading_candidates():
    site = SiteSpec(
        name="skewed",
        boundary=[(0, 0), (30, 0), (44, 44), (14, 44)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0, 20], "entrance_offsets": [0]},
    )

    layout = generate_layout(site)

    assert {attempt.heading_delta_degrees for attempt in layout.attempts} == {0.0, 20.0}
    assert layout.selected_heading_delta_degrees in {0.0, 20.0}
    assert layout.stall_count == max(attempt.stall_count for attempt in layout.attempts)


def test_phase1_layout_evaluates_entrance_offset_candidates():
    site = SiteSpec(
        name="offsets",
        boundary=[(0, 0), (30, 0), (30, 34), (0, 34)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(15, 0),
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [-1, 0, 1]},
    )

    layout = generate_layout(site)

    assert {attempt.entrance_offset for attempt in layout.attempts} == {-1.0, 0.0, 1.0}
    assert layout.selected_entrance_offset in {-1.0, 0.0, 1.0}
    assert layout.stall_count == max(attempt.stall_count for attempt in layout.attempts)


def test_phase1_layout_can_add_single_branch_candidate():
    common = dict(
        name="branch",
        boundary=[(0, 0), (60, 0), (60, 50), (0, 50)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(30, 0),
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
    )
    base = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False})
    branched = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_positions": [24]})

    base_layout = generate_layout(base)
    branch_layout = generate_layout(branched)

    assert branch_layout.stall_count > base_layout.stall_count
    assert branch_layout.selected_branch_side in {"left", "right"}
    assert any(aisle.id == "A-BRANCH-001" for aisle in branch_layout.aisles)


def test_phase1_branch_attempts_report_candidate_reasons():
    site = SiteSpec(
        name="branch-report",
        boundary=[(0, 0), (64, 0), (64, 34), (50, 45), (10, 39), (0, 28)],
        stall=StallSpec(width=2.5, length=5.3, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.2,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(8, 0),
                width=7.0,
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
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_step": 2.5},
    )

    layout = generate_layout(site)

    assert layout.attempts[0].branch_candidates
    assert {item["reason"] for item in layout.attempts[0].branch_candidates}


def test_diagnostics_reads_phase1_support_without_generator_dependency():
    assert diagnostics.phase1_unsupported_inputs is phase1_unsupported_inputs


def test_generate_layout_preserves_branch_toggle_pipeline_metadata():
    common = dict(
        name="branch-toggle",
        boundary=[(0, 0), (60, 0), (60, 50), (0, 50)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(30, 0),
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
    )
    disabled = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False})
    enabled = SiteSpec(**common, optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_positions": [24]})

    disabled_layout = generate_layout(disabled)
    enabled_layout = generate_layout(enabled)

    assert disabled_layout.selected_branch_side is None
    assert disabled_layout.graph_validation["valid"] is True
    assert disabled_layout.attempts[0].branch_candidates == []
    assert enabled_layout.selected_branch_side in {"left", "right"}
    assert enabled_layout.graph_validation["valid"] is True
    assert enabled_layout.attempts[0].graph_valid is True
    assert enabled_layout.attempts[0].branch_candidates
    assert enabled_layout.stall_count > disabled_layout.stall_count
