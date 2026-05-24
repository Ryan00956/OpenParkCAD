from openparkcad.generator import generate_layout
from openparkcad.models import SiteSpec, StallSpec


def test_generate_layout_places_stalls_inside_simple_lot():
    site = SiteSpec(
        name="test",
        boundary=[(0, 0), (20, 0), (20, 14), (0, 14)],
        stall=StallSpec(width=2.5, length=5.0),
        aisle_width=6.0,
        margin=0.0,
    )

    layout = generate_layout(site)

    assert layout.stall_count > 0
    assert len(layout.aisles) > 0
    assert all(stall.id.startswith("P-") for stall in layout.stalls)


def test_generate_layout_selects_best_candidate_angle():
    site = SiteSpec(
        name="angled",
        boundary=[(0, 0), (28, 0), (28, 16), (0, 16)],
        stall=StallSpec(width=2.5, length=5.0),
        aisle_width=6.0,
        candidate_angles=(0.0, 90.0),
        margin=0.0,
    )

    layout = generate_layout(site)

    assert {attempt.angle_degrees for attempt in layout.attempts} == {0.0, 90.0}
    assert layout.stall_count == max(attempt.stall_count for attempt in layout.attempts)
