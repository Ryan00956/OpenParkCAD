from openparkcad.generator import generate_layout
from openparkcad.layout_geometry import available_area
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec


def test_excessive_boundary_setback_produces_no_usable_area_or_layout():
    site = SiteSpec(
        name="setback-consumes-site",
        boundary=[(0, 0), (10, 0), (10, 10), (0, 10)],
        margin=6.0,
        stall=StallSpec(allowed_angles=(90.0,)),
        entrances=[EntranceSpec("gate", "shared", (5, 0), 7.0, 90.0)],
        aisle_classes=[AisleClassSpec("wide", 6.0)],
        fixed_aisle_class="wide",
    )

    assert available_area(site).is_empty
    assert generate_layout(site).stall_count == 0

