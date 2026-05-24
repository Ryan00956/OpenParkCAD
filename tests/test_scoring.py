from openparkcad.generator import generate_layout
from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec, StallSpec
from openparkcad.scoring import score_layout


def _site(objective="balanced", weights=None):
    optimization = {
        "objective": objective,
        "heading_deltas_degrees": [0],
        "entrance_offsets": [0],
        "enable_branches": False,
    }
    if weights:
        optimization["weights"] = weights
    return SiteSpec(
        name="score",
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
        optimization=optimization,
    )


def test_score_layout_has_explainable_breakdown():
    layout = generate_layout(_site())

    score = score_layout(layout)

    assert score["total"] == layout.score["total"]
    assert score["stall_value"] > 0
    assert score["aisle_area_penalty"] < 0
    assert "dead_end_penalty" in score


def test_score_weights_can_be_overridden():
    layout = generate_layout(_site(weights={"stall_count": 10, "aisle_area": 0, "dead_end_length": 0}))

    assert layout.score["stall_value"] == layout.stall_count * 10
    assert layout.score["aisle_area_penalty"] == 0
    assert layout.score["dead_end_penalty"] == 0
