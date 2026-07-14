from dataclasses import replace

from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, ParkingAisle, ParkingStall, SiteSpec, StallSpec
from openparkcad.traffic_graph import build_traffic_graph, traffic_graph_report, validate_traffic_graph


def _site(**overrides) -> SiteSpec:
    data = {
        "name": "graph",
        "boundary": [(0, 0), (36, 0), (36, 38), (0, 38)],
        "stall": StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        "aisle_width": 6.0,
        "margin": 0.0,
        "entrances": [
            EntranceSpec(
                id="main",
                mode="shared",
                center=(18, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        "aisle_classes": [
            AisleClassSpec(
                id="wide-two-way-no-cross",
                width=6.0,
                capacity="two_vehicle",
                directionality="two_way",
            )
        ],
        "fixed_aisle_class": "wide-two-way-no-cross",
        "optimization": {"heading_deltas_degrees": [0], "entrance_offsets": [0], "enable_branches": False},
    }
    data.update(overrides)
    return SiteSpec(**data)


def test_traffic_graph_validates_phase1_main_aisle_layout():
    layout = generate_layout(_site())

    report = traffic_graph_report(layout)
    validation = report["validation"]

    assert validation["valid"] is True
    assert validation["reachable_aisles"] == ["A-MAIN", "A-TURNAROUND"]
    assert validation["unreachable_aisles"] == []
    assert validation["unreachable_stalls"] == []
    assert validation["stalls_without_exit_path"] == []
    assert report["nodes"]
    assert report["edges"]
    assert len(report["stall_access"]) == layout.stall_count
    assert validation["dead_ends"][0]["status"] == "allowed_with_turnaround"


def test_traffic_graph_validates_selected_branch_layout():
    site = _site(
        boundary=[(0, 0), (60, 0), (60, 50), (0, 50)],
        entrances=[
            EntranceSpec(
                id="main",
                mode="shared",
                center=(30, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
        optimization={"heading_deltas_degrees": [0], "entrance_offsets": [0], "branch_start_positions": [24]},
    )
    layout = generate_layout(site)

    validation = validate_traffic_graph(build_traffic_graph(layout), layout)

    assert validation["valid"] is True
    assert "A-BRANCH-001" in validation["reachable_aisles"]
    assert any(access.served_by_aisle_id == "A-BRANCH-001" for access in layout.stalls)
    assert {item["status"] for item in validation["dead_ends"]} == {"allowed_with_turnaround"}


def test_traffic_graph_reports_unreachable_aisle_fragment():
    layout = generate_layout(_site())
    orphan = ParkingAisle(
        id="A-ORPHAN",
        polygon=[(30, 30), (34, 30), (34, 34), (30, 34)],
        angle_degrees=0.0,
        role="branch",
        parent_aisle_id="A-MISSING",
    )
    broken = replace(layout, aisles=[*layout.aisles, orphan])

    validation = validate_traffic_graph(build_traffic_graph(broken), broken)

    assert validation["valid"] is False
    assert "edge_references_missing_node" in validation["errors"]
    assert "unreachable_aisles" in validation["errors"]
    assert validation["unreachable_aisles"] == ["A-ORPHAN"]


def test_traffic_graph_reports_stall_with_missing_aisle():
    layout = generate_layout(_site())
    bad_stall = ParkingStall(
        id="P-BAD",
        polygon=[(8, 8), (10, 8), (10, 13), (8, 13)],
        angle_degrees=90.0,
        served_by_aisle_id="A-MISSING",
        aisle_side="left",
    )
    broken = replace(layout, stalls=[*layout.stalls, bad_stall])

    validation = validate_traffic_graph(build_traffic_graph(broken), broken)

    assert validation["valid"] is False
    assert "stall_references_missing_aisle" in validation["errors"]
    assert validation["stalls_missing_aisles"] == ["P-BAD"]
    assert "P-BAD" in validation["unreachable_stalls"]


def test_traffic_graph_requires_exit_path_for_stalls():
    layout = generate_layout(_site())
    entry_only_site = replace(
        layout.site,
        entrances=[
            EntranceSpec(
                id="main",
                mode="entry_only",
                center=(18, 0),
                width=8.0,
                heading_degrees=90.0,
            )
        ],
    )
    broken = replace(layout, site=entry_only_site)

    validation = validate_traffic_graph(build_traffic_graph(broken), broken)

    assert validation["valid"] is False
    assert "stalls_without_exit_path" in validation["errors"]
    assert validation["stalls_without_exit_path"] == [stall.id for stall in broken.stalls]
