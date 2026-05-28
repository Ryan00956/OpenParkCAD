from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.generator import generate_layout
from openparkcad.maneuver_validation import apply_maneuver_filter, validate_maneuvers
from openparkcad.models import AisleClassSpec, EntranceSpec, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec


def test_phase3a_maneuver_validation_rejects_blocked_access_envelope():
    site = SiteSpec(
        name="blocked-access",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        obstacles=[[(1.25, 4), (2.75, 4), (2.75, 5), (1.25, 5)]],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(1, 6), (3.5, 6), (3.5, 11), (1, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    validation = validate_maneuvers(layout)

    assert validation["valid"] is False
    assert validation["invalid_stalls"][0]["stall_id"] == "P-001"
    assert validation["invalid_stalls"][0]["reason"] == "access_envelope_hits_boundary_or_obstacle"


def test_phase3a_generator_filters_stalls_without_required_access_depth():
    site = SiteSpec(
        name="strict-access-depth",
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
        optimization={
            "heading_deltas_degrees": [0],
            "entrance_offsets": [0],
            "maneuver_access_depth": 7.0,
        },
    )

    layout = generate_layout(site)

    assert layout.stall_count == 0
    assert layout.maneuver_validation["valid"] is True
    assert layout.maneuver_validation["filtered_stall_count"] > 0
    assert all(ShapelyPolygon(stall.polygon).is_valid for stall in layout.stalls)


def test_phase3a_filter_keeps_valid_access_envelopes():
    site = SiteSpec(
        name="valid-access",
        boundary=[(0, 0), (20, 0), (20, 20), (0, 20)],
        stall=StallSpec(width=2.5, length=5.0, allowed_angles=(90.0,)),
        aisle_width=6.0,
        margin=0.0,
    )
    layout = LayoutResult(
        site=site,
        stalls=[
            ParkingStall(
                id="P-001",
                polygon=[(1, 6), (3.5, 6), (3.5, 11), (1, 11)],
                angle_degrees=90.0,
                served_by_aisle_id="A-MAIN",
                aisle_side="left",
            )
        ],
        aisles=[
            ParkingAisle(
                id="A-MAIN",
                polygon=[(0, 0), (10, 0), (10, 6), (0, 6)],
                angle_degrees=90.0,
                role="main",
            )
        ],
    )

    filtered = apply_maneuver_filter(layout)

    assert filtered.stall_count == 1
    assert filtered.maneuver_validation["valid"] is True
