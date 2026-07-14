from __future__ import annotations

from dataclasses import dataclass
from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import LayoutResult, SiteSpec
from openparkcad.operational_quality import operational_risk_score


@dataclass(frozen=True)
class ScoreWeights:
    stall_count: float
    aisle_area: float
    heading_delta: float
    entrance_offset: float
    branch_count: float
    dead_end_length: float
    operational_risk: float


DEFAULT_WEIGHTS = {
    "max_stalls": ScoreWeights(
        stall_count=120.0,
        aisle_area=-0.4,
        heading_delta=-1.0,
        entrance_offset=-1.0,
        branch_count=-5.0,
        dead_end_length=-0.2,
        operational_risk=-0.1,
    ),
    "balanced": ScoreWeights(
        stall_count=100.0,
        aisle_area=-1.0,
        heading_delta=-2.0,
        entrance_offset=-3.0,
        branch_count=-15.0,
        dead_end_length=-0.5,
        operational_risk=-0.25,
    ),
    "conservative": ScoreWeights(
        stall_count=90.0,
        aisle_area=-1.5,
        heading_delta=-4.0,
        entrance_offset=-6.0,
        branch_count=-30.0,
        dead_end_length=-1.0,
        operational_risk=-1.0,
    ),
}


def score_layout(layout: LayoutResult) -> dict[str, float]:
    metrics = _metrics(layout)
    return score_metrics(layout.site, metrics)


def score_metrics(site: SiteSpec, metrics: dict[str, float]) -> dict[str, float]:
    weights = _weights_for_site(site)
    stall_value = metrics["stall_count"] * weights.stall_count
    aisle_area_penalty = metrics["aisle_area"] * weights.aisle_area
    heading_penalty = metrics["heading_delta"] * weights.heading_delta
    entrance_offset_penalty = metrics["entrance_offset"] * weights.entrance_offset
    branch_penalty = metrics["branch_count"] * weights.branch_count
    dead_end_penalty = metrics["dead_end_length"] * weights.dead_end_length
    operational_risk_penalty = metrics.get("operational_risk", 0.0) * weights.operational_risk
    total = (
        stall_value
        + aisle_area_penalty
        + heading_penalty
        + entrance_offset_penalty
        + branch_penalty
        + dead_end_penalty
        + operational_risk_penalty
    )

    return {
        "total": total,
        "stall_value": stall_value,
        "aisle_area_penalty": aisle_area_penalty,
        "heading_penalty": heading_penalty,
        "entrance_offset_penalty": entrance_offset_penalty,
        "branch_penalty": branch_penalty,
        "dead_end_penalty": dead_end_penalty,
        "operational_risk_penalty": operational_risk_penalty,
        "stall_count": metrics["stall_count"],
        "aisle_area": metrics["aisle_area"],
        "heading_delta": metrics["heading_delta"],
        "entrance_offset": metrics["entrance_offset"],
        "branch_count": metrics["branch_count"],
        "dead_end_length": metrics["dead_end_length"],
        "operational_risk": metrics.get("operational_risk", 0.0),
    }


def score_total(layout: LayoutResult) -> float:
    return layout.score.get("total", score_layout(layout)["total"])


def _weights_for_site(site: SiteSpec) -> ScoreWeights:
    objective = str(site.optimization.get("objective", "balanced"))
    base = DEFAULT_WEIGHTS.get(objective, DEFAULT_WEIGHTS["balanced"])
    overrides = site.optimization.get("weights", {})
    if not isinstance(overrides, dict):
        overrides = {}
    return ScoreWeights(
        stall_count=float(overrides.get("stall_count", base.stall_count)),
        aisle_area=float(overrides.get("aisle_area", base.aisle_area)),
        heading_delta=float(overrides.get("heading_delta", base.heading_delta)),
        entrance_offset=float(overrides.get("entrance_offset", base.entrance_offset)),
        branch_count=float(overrides.get("branch_count", base.branch_count)),
        dead_end_length=float(overrides.get("dead_end_length", base.dead_end_length)),
        operational_risk=float(overrides.get("operational_risk", base.operational_risk)),
    )


def _metrics(layout: LayoutResult) -> dict[str, float]:
    aisle_area = sum(ShapelyPolygon(aisle.polygon).area for aisle in layout.aisles)
    branch_count = float(len([aisle for aisle in layout.aisles if aisle.role == "branch"]))
    dead_end_length = sum(_dead_end_lengths(layout))
    return {
        "stall_count": float(layout.stall_count),
        "aisle_area": aisle_area,
        "heading_delta": abs(layout.selected_heading_delta_degrees),
        "entrance_offset": abs(layout.selected_entrance_offset),
        "branch_count": branch_count,
        "dead_end_length": dead_end_length,
        "operational_risk": operational_risk_score(layout),
    }


def _dead_end_lengths(layout: LayoutResult) -> list[float]:
    lengths = []
    connected_branch_ids = {
        branch_id
        for connector in layout.selected_connectors
        for branch_id in connector.get("connects", [])
        if isinstance(branch_id, str)
    }
    if layout.aisles:
        main = next((aisle for aisle in layout.aisles if aisle.id == "A-MAIN"), None)
        if main:
            lengths.append(_long_side_length(main.polygon))
    if layout.selected_branch_length and not layout.selected_branches:
        lengths.append(layout.selected_branch_length)
    for branch in layout.selected_branches:
        if branch.get("id") in connected_branch_ids:
            continue
        length = branch.get("length")
        if isinstance(length, int | float):
            lengths.append(float(length))
    return lengths


def _long_side_length(poly) -> float:
    points = list(poly)
    if len(points) < 2:
        return 0.0
    lengths = []
    for start, end in zip(points, points[1:] + points[:1]):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        lengths.append((dx * dx + dy * dy) ** 0.5)
    return max(lengths or [0.0])
