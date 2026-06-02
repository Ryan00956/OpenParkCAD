from openparkcad.models import LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec
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


def test_phase5a_operational_quality_reports_junction_stall_conflicts():
    report = operational_quality_report(_quality_layout())

    assert report["version"] == "phase5a-1"
    assert report["status"] == "report_only"
    assert report["valid"] is True
    assert report["junction_count"] == 1
    assert report["junction_conflict_count"] == 1
    assert report["risk_score"] == 1.0
    assert report["junctions"][0]["conflicting_stalls"][0]["stall_id"] == "P-001"


def test_phase5a_operational_risk_penalty_is_scoreable():
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
