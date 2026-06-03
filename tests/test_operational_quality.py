from openparkcad.models import AisleClassSpec, EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.operational_quality import operational_quality_report
from openparkcad.scoring import score_layout


def _quality_layout(site: SiteSpec | None = None) -> LayoutResult:
    return LayoutResult(
        site=site
        or SiteSpec(
            name="operational-quality",
            boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
            stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
            aisle_width=6.0,
            margin=0.0,
        ),
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
            ),
            ParkingAisle(
                id="A-BRANCH-001",
                polygon=[(8, 4), (14, 4), (14, 20), (8, 20)],
                angle_degrees=90.0,
                role="branch",
                parent_aisle_id="A-MAIN",
            ),
        ],
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(13, 7), (15.5, 7), (15.5, 12), (13, 12)],
                angle_degrees=90.0,
                served_by_aisle_id="A-BRANCH-001",
                aisle_side="right",
            )
        ],
    )


def _route_layout(optimization: dict | None = None) -> LayoutResult:
    site = SiteSpec(
        name="route-quality",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(0, 7),
                width=7.0,
                heading_degrees=0.0,
            )
        ],
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="main",
            ),
            ParkingAisle(
                id="A-TURNAROUND",
                polygon=[(20, 4), (26, 4), (26, 10), (20, 10)],
                angle_degrees=0.0,
                role="turnaround",
                parent_aisle_id="A-MAIN",
            ),
        ],
        stalls=[
            ParkingStall(
                id="P-ROUTE-001",
                polygon=[(8, 10), (10.5, 10), (10.5, 15), (8, 15)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def _directional_trap_layout(optimization: dict | None = None) -> LayoutResult:
    site = SiteSpec(
        name="directional-trap",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="entry",
                mode="entry_only",
                center=(0, 7),
                width=7.0,
                heading_degrees=0.0,
            ),
            EntranceSpec(
                id="exit",
                mode="exit_only",
                center=(30, 7),
                width=7.0,
                heading_degrees=180.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="wide-one-way",
                width=6.0,
                directionality="one_way",
            )
        ],
        fixed_aisle_class="wide-one-way",
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 10), (0, 10)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="entry",
            )
        ],
        stalls=[
            ParkingStall(
                id="P-DIR-001",
                polygon=[(8, 10), (10.5, 10), (10.5, 15), (8, 15)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def _narrow_two_way_layout(
    optimization: dict | None = None,
    site_features: list[dict] | None = None,
) -> LayoutResult:
    site = SiteSpec(
        name="narrow-two-way",
        boundary=[(0, 0), (30, 0), (30, 18), (0, 18)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=3.5,
        margin=0.0,
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(0, 7),
                width=4.0,
                heading_degrees=0.0,
            )
        ],
        aisle_classes=[
            AisleClassSpec(
                id="narrow-two-way",
                width=3.5,
                capacity="single_vehicle",
                directionality="two_way",
            )
        ],
        fixed_aisle_class="narrow-two-way",
        site_features=site_features or [],
        optimization=optimization or {},
    )
    return LayoutResult(
        site=site,
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 4), (20, 4), (20, 7.5), (0, 7.5)],
                angle_degrees=0.0,
                role="main",
                connected_to_entrance_id="main",
            )
        ],
        stalls=[
            ParkingStall(
                id="P-NARROW-001",
                polygon=[(8, 7.5), (10.5, 7.5), (10.5, 12.5), (8, 12.5)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
    )


def test_phase5j_operational_quality_reports_junction_stall_conflicts():
    report = operational_quality_report(_quality_layout())

    assert report["version"] == "phase5j-1"
    assert report["status"] == "report_only"
    assert report["mode"] == "score_only"
    assert report["valid"] is True
    assert report["junction_count"] == 1
    assert report["junction_conflict_count"] == 1
    assert report["risk_score"] == 1.0
    assert report["route_risk_score"] == 0.0
    assert report["directionality_risk_score"] == 0.0
    assert report["narrow_two_way_risk_score"] == 0.0
    assert report["route_summary"]["checked_stall_count"] == 0
    assert report["directionality_summary"]["checked_stall_count"] == 1
    assert report["narrow_two_way_summary"]["is_narrow_two_way"] is False
    assert report["route_summary_risks"] == []
    assert report["promotion_blockers"] == []
    assert report["blocking_conflicts"] == []
    assert report["junctions"][0]["conflicting_stalls"][0]["stall_id"] == "P-001"


def test_phase5g_operational_quality_reports_route_lengths_without_default_penalty():
    report = operational_quality_report(_route_layout())

    assert report["route_risks"]["status"] == "active"
    assert report["route_risks"]["version"] == "phase5f-1"
    assert report["route_risks"]["checked_stall_count"] == 1
    assert report["route_risk_score"] == 0.0
    assert report["route_risks"]["stall_route_risk_score"] == 0.0
    assert report["route_risks"]["summary_risk_score"] == 0.0
    assert report["route_summary_risks"] == []
    summary = report["route_summary"]
    assert summary["checked_stall_count"] == 1
    assert summary["average_route_length"] == 20.0
    assert summary["max_route_length"] == 20.0
    assert summary["max_entry_path_length"] == 10.0
    assert summary["max_exit_path_length"] == 10.0
    assert summary["longest_route_stall_id"] == "P-ROUTE-001"
    assert summary["turnaround_dependency_count"] == 1
    assert summary["turnaround_dependency_ratio"] == 1.0
    assert summary["long_route_ratio"] == 0.0
    assert summary["issue_counts"] == {}
    route = report["route_risks"]["routes"][0]
    assert route["stall_id"] == "P-ROUTE-001"
    assert route["entry_path_length"] == 10.0
    assert route["exit_path_length"] == 10.0
    assert route["route_length"] == 20.0
    assert route["depends_on_dead_end_turnaround"] is True
    assert route["issues"] == []


def test_phase5g_operational_directionality_reports_trap_without_default_penalty():
    report = operational_quality_report(_directional_trap_layout())

    assert report["directionality_risks"]["status"] == "active"
    assert report["directionality_risks"]["version"] == "phase5g-1"
    assert report["directionality_risk_score"] == 0.0
    assert report["directionality_summary"]["node_issue_count"] == 1
    assert report["directionality_summary"]["stall_issue_count"] == 1
    assert report["directionality_summary"]["stall_issue_ratio"] == 1.0
    assert report["directionality_summary"]["one_way_trap_node_count"] == 1
    assert report["directionality_risks"]["node_issues"][0]["issue"] == "one_way_trap"
    assert report["directionality_risks"]["stall_issues"][0]["issue"] == "stall_on_one_way_trap"
    assert report["directionality_summary_risks"] == []


def test_phase5g_operational_directionality_issue_can_gate_promotion():
    layout = _directional_trap_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_directionality_issue_risk": 1,
            "operational_missing_route_risk": 0,
        }
    )

    report = operational_quality_report(layout)

    assert report["directionality_risk_score"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "directionality_stall")
    assert conflict["stall_id"] == "P-DIR-001"
    assert conflict["issue"] == "stall_on_one_way_trap"


def test_phase5g_operational_directionality_issue_ratio_can_gate_promotion():
    layout = _directional_trap_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_directionality_issue_ratio": 0.5,
            "operational_missing_route_risk": 0,
        }
    )

    report = operational_quality_report(layout)

    assert report["directionality_risk_score"] == 1.0
    assert report["directionality_summary_risks"][0]["issue"] == "directionality_stall_issue_ratio_exceeds_limit"
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "directionality_summary")
    assert conflict["stall_issue_ratio"] == 1.0


def test_phase5j_operational_narrow_two_way_reports_without_default_penalty():
    report = operational_quality_report(_narrow_two_way_layout())

    assert report["narrow_two_way_risks"]["status"] == "active"
    assert report["narrow_two_way_risks"]["version"] == "phase5j-1"
    assert report["narrow_two_way_risk_score"] == 0.0
    assert report["narrow_two_way_summary"]["is_narrow_two_way"] is True
    assert report["narrow_two_way_summary"]["narrow_two_way_aisle_count"] == 1
    assert report["narrow_two_way_summary"]["affected_stall_count"] == 1
    assert report["narrow_two_way_summary"]["affected_stall_ratio"] == 1.0
    assert report["narrow_two_way_summary"]["passing_bay_model_available"] is False
    assert report["narrow_two_way_summary"]["passing_bay_marker_count"] == 0
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 0
    assert report["narrow_two_way_risks"]["passing_bays"] == []
    assert report["narrow_two_way_risks"]["aisle_issues"][0]["issue"] == "narrow_two_way_without_passing_bay_model"
    assert report["narrow_two_way_risks"]["stall_issues"][0]["issue"] == "stall_served_by_narrow_two_way_aisle_without_passing_bay_model"
    assert report["narrow_two_way_summary_risks"] == []


def test_phase5j_operational_narrow_two_way_detects_usable_passing_bay_markers():
    layout = _narrow_two_way_layout(
        site_features=[
            {
                "id": "bay-1",
                "type": "passing-bay",
                "aisle_id": "A-MAIN",
                "center": [12.0, 5.75],
                "width": 2.5,
                "length": 6.0,
            }
        ]
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_summary"]["passing_bay_model_available"] is True
    assert report["narrow_two_way_summary"]["passing_bay_marker_count"] == 1
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 1
    assert report["narrow_two_way_summary"]["invalid_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 0
    assert report["narrow_two_way_risks"]["passing_bays"][0]["id"] == "bay-1"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["type"] == "passing_bay"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["usable"] is True
    assert report["narrow_two_way_risks"]["passing_bays"][0]["associated_aisle_id"] == "A-MAIN"
    assert report["narrow_two_way_risks"]["passing_bays"][0]["geometry_source"] == "center_width_length"
    assert report["narrow_two_way_risks"]["aisle_issues"][0]["issue"] == "narrow_two_way_passing_bay_spacing_not_checked"
    assert report["narrow_two_way_risks"]["stall_issues"][0]["issue"] == "stall_served_by_narrow_two_way_aisle_pending_passing_bay_spacing_check"
    assert report["narrow_two_way_summary_risks"] == []


def test_phase5j_operational_passing_bay_shortage_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_min_passing_bays": 2,
            "operational_passing_bay_shortage_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 2.0
    assert report["narrow_two_way_summary"]["min_passing_bays"] == 2
    assert report["narrow_two_way_summary"]["passing_bay_shortage_count"] == 2
    assert report["narrow_two_way_summary_risks"][0]["issue"] == "passing_bay_count_below_minimum"
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_summary")
    assert conflict["passing_bay_marker_count"] == 0
    assert conflict["usable_passing_bay_count"] == 0
    assert conflict["min_passing_bays"] == 2
    assert conflict["passing_bay_shortage_count"] == 2


def test_phase5j_operational_passing_bay_geometry_issue_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_passing_bay_geometry_issue_risk": 1,
        },
        site_features=[
            {
                "id": "bay-far",
                "type": "passing_bay",
                "aisle_id": "A-MAIN",
                "center": [25.0, 15.0],
                "width": 2.5,
                "length": 6.0,
            }
        ],
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary"]["usable_passing_bay_count"] == 0
    assert report["narrow_two_way_summary"]["invalid_passing_bay_count"] == 1
    passing_bay = report["narrow_two_way_risks"]["passing_bays"][0]
    assert passing_bay["usable"] is False
    assert passing_bay["issues"] == ["passing_bay_not_adjacent_to_aisle"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "passing_bay")
    assert conflict["passing_bay_id"] == "bay-far"
    assert conflict["issues"] == ["passing_bay_not_adjacent_to_aisle"]


def test_phase5j_operational_narrow_two_way_issue_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_narrow_two_way_issue_risk": 1,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_stall")
    assert conflict["stall_id"] == "P-NARROW-001"
    assert conflict["issue"] == "stall_served_by_narrow_two_way_aisle_without_passing_bay_model"


def test_phase5j_operational_narrow_two_way_stall_ratio_can_gate_promotion():
    layout = _narrow_two_way_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_narrow_two_way_stall_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["narrow_two_way_risk_score"] == 1.0
    assert report["narrow_two_way_summary_risks"][0]["issue"] == "narrow_two_way_stall_ratio_exceeds_limit"
    conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "narrow_two_way_summary")
    assert conflict["affected_stall_ratio"] == 1.0


def test_phase5g_operational_route_risk_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_route_length": 5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_summary"]["route_length_exceeds_limit_count"] == 1
    assert report["route_summary"]["long_route_ratio"] == 1.0
    assert report["route_summary"]["issue_counts"] == {"route_length_exceeds_limit": 1}
    assert report["risk_exceeds_limit"] is True
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    route_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "stall_route")
    assert route_conflict["stall_id"] == "P-ROUTE-001"
    assert route_conflict["issues"] == ["route_length_exceeds_limit"]


def test_phase5g_operational_turnaround_dependency_ratio_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_turnaround_dependency_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_risks"]["stall_route_risk_score"] == 0.0
    assert report["route_risks"]["summary_risk_score"] == 1.0
    assert report["route_summary_risks"][0]["issue"] == "turnaround_dependency_ratio_exceeds_limit"
    assert report["route_summary_risks"][0]["turnaround_dependency_ratio"] == 1.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    summary_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "route_summary")
    assert summary_conflict["issue"] == "turnaround_dependency_ratio_exceeds_limit"


def test_phase5g_operational_average_route_length_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_average_route_length": 10,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 1.0
    assert report["route_summary_risks"][0]["issue"] == "average_route_length_exceeds_limit"
    assert report["route_summary_risks"][0]["average_route_length"] == 20.0
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    summary_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "route_summary")
    assert summary_conflict["issue"] == "average_route_length_exceeds_limit"


def test_phase5g_operational_long_route_ratio_can_gate_promotion():
    layout = _route_layout(
        {
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
            "operational_max_route_length": 5,
            "operational_max_long_route_ratio": 0.5,
        }
    )

    report = operational_quality_report(layout)

    assert report["route_risk_score"] == 2.0
    assert [item["issue"] for item in report["route_summary_risks"]] == ["long_route_ratio_exceeds_limit"]
    assert report["route_summary_risks"][0]["long_route_ratio"] == 1.0
    summary_conflict = next(
        item
        for item in report["blocking_conflicts"]
        if item["source_type"] == "route_summary"
    )
    assert summary_conflict["issue"] == "long_route_ratio_exceeds_limit"


def test_phase5g_operational_quality_score_only_does_not_block_with_limit():
    site = SiteSpec(
        name="operational-score-only",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "score_only",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["risk_exceeds_limit"] is True
    assert report["valid"] is True
    assert report["promotion_blockers"] == []
    assert report["blocking_conflicts"] == []


def test_phase5g_operational_quality_promotion_gate_reports_blockers():
    site = SiteSpec(
        name="operational-gate",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "promotion_gate",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["valid"] is True
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    assert report["blocking_conflicts"][0]["source_type"] == "junction"
    assert report["blocking_conflicts"][0]["stall_id"] == "P-001"


def test_phase5g_operational_quality_hard_reject_marks_layout_invalid():
    site = SiteSpec(
        name="operational-hard-reject",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "operational_quality_mode": "hard_reject",
            "operational_max_risk_score": 0,
        },
    )

    report = operational_quality_report(_quality_layout(site))

    assert report["status"] == "active_failed"
    assert report["valid"] is False
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]


def test_phase5g_operational_risk_penalty_is_scoreable():
    site = SiteSpec(
        name="operational-risk-score",
        boundary=[(0, 0), (24, 0), (24, 24), (0, 24)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
        optimization={
            "weights": {
                "stall_count": 0,
                "aisle_area": 0,
                "heading_delta": 0,
                "entrance_offset": 0,
                "branch_count": 0,
                "dead_end_length": 0,
                "operational_risk": -2,
            }
        },
    )
    layout = _quality_layout(site)

    score = score_layout(layout)

    assert score["operational_risk"] == 1.0
    assert score["operational_risk_penalty"] == -2.0
    assert score["total"] == -2.0
