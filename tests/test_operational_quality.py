from openparkcad.models import EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
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


def test_phase5d_operational_quality_reports_junction_stall_conflicts():
    report = operational_quality_report(_quality_layout())

    assert report["version"] == "phase5d-1"
    assert report["status"] == "report_only"
    assert report["mode"] == "score_only"
    assert report["valid"] is True
    assert report["junction_count"] == 1
    assert report["junction_conflict_count"] == 1
    assert report["risk_score"] == 1.0
    assert report["route_risk_score"] == 0.0
    assert report["route_summary"]["checked_stall_count"] == 0
    assert report["promotion_blockers"] == []
    assert report["blocking_conflicts"] == []
    assert report["junctions"][0]["conflicting_stalls"][0]["stall_id"] == "P-001"


def test_phase5d_operational_quality_reports_route_lengths_without_default_penalty():
    report = operational_quality_report(_route_layout())

    assert report["route_risks"]["status"] == "active"
    assert report["route_risks"]["version"] == "phase5d-1"
    assert report["route_risks"]["checked_stall_count"] == 1
    assert report["route_risk_score"] == 0.0
    summary = report["route_summary"]
    assert summary["checked_stall_count"] == 1
    assert summary["average_route_length"] == 20.0
    assert summary["max_route_length"] == 20.0
    assert summary["max_entry_path_length"] == 10.0
    assert summary["max_exit_path_length"] == 10.0
    assert summary["longest_route_stall_id"] == "P-ROUTE-001"
    assert summary["turnaround_dependency_count"] == 1
    assert summary["turnaround_dependency_ratio"] == 1.0
    assert summary["issue_counts"] == {}
    route = report["route_risks"]["routes"][0]
    assert route["stall_id"] == "P-ROUTE-001"
    assert route["entry_path_length"] == 10.0
    assert route["exit_path_length"] == 10.0
    assert route["route_length"] == 20.0
    assert route["depends_on_dead_end_turnaround"] is True
    assert route["issues"] == []


def test_phase5d_operational_route_risk_can_gate_promotion():
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
    assert report["route_summary"]["issue_counts"] == {"route_length_exceeds_limit": 1}
    assert report["risk_exceeds_limit"] is True
    assert report["promotion_blockers"] == ["operational_quality_risk_exceeds_limit"]
    route_conflict = next(item for item in report["blocking_conflicts"] if item["source_type"] == "stall_route")
    assert route_conflict["stall_id"] == "P-ROUTE-001"
    assert route_conflict["issues"] == ["route_length_exceeds_limit"]


def test_phase5d_operational_quality_score_only_does_not_block_with_limit():
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


def test_phase5d_operational_quality_promotion_gate_reports_blockers():
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


def test_phase5d_operational_quality_hard_reject_marks_layout_invalid():
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


def test_phase5d_operational_risk_penalty_is_scoreable():
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
