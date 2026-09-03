"""Isolated per-spine layout candidate identity and copies."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle, ParkingStall, SiteSpec, StallSpec

SPINE_ROLES = frozenset({"main", "jog", "turnaround", "exit", "passing_bay"})


@dataclass(frozen=True)
class LayoutCandidateContext:
    candidate_id: str
    spine_id: str
    site: SiteSpec
    template_layout: LayoutResult
    branch_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    collect_status: str = "collected"
    reject_reason: str | None = None
    spine_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LayoutCandidateEvaluation:
    candidate_id: str
    spine_id: str
    requested_backend: str | None
    actual_backend: str | None
    fallback_reason: str | None
    preview: dict[str, Any]
    rebuilt_layout: LayoutResult | None
    checks: dict[str, Any]
    score: dict[str, Any] | None
    duration_seconds: float | None
    failure_class: str | None
    used_template: bool
    provenance: dict[str, Any] = field(default_factory=dict)


def canonical_dumps(value: Any) -> str:
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_dumps(value).encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return float(value)
    if isinstance(value, StallSpec):
        return {
            "id": value.id,
            "family": value.family,
            "width": value.width,
            "length": value.length,
            "allowed_angles": list(value.allowed_angles),
            "classifications": list(value.classifications),
        }
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    return str(value)


def copy_site(site: SiteSpec) -> SiteSpec:
    return replace(
        site,
        obstacles=[list(polygon) for polygon in site.obstacles],
        site_features=copy.deepcopy(list(site.site_features)),
        aisle_classes=list(site.aisle_classes),
        entrances=list(site.entrances),
        standards=copy.deepcopy(dict(site.standards)),
        optimization=copy.deepcopy(dict(site.optimization)),
        constraints=copy.deepcopy(dict(site.constraints)),
        parking_quotas=copy.deepcopy(dict(site.parking_quotas)),
        pedestrian_and_emergency=copy.deepcopy(dict(site.pedestrian_and_emergency)),
        diagnostics=copy.deepcopy(dict(site.diagnostics)),
        metadata=copy.deepcopy(dict(site.metadata)),
        stall_candidates=tuple(site.stall_candidates),
    )


def copy_stall(stall: ParkingStall) -> ParkingStall:
    return replace(stall, polygon=list(stall.polygon))


def copy_aisle(aisle: ParkingAisle) -> ParkingAisle:
    return replace(aisle, polygon=list(aisle.polygon), connected_aisle_ids=tuple(aisle.connected_aisle_ids))


def copy_candidate_object(item: CandidateObject) -> CandidateObject:
    return replace(
        item,
        geometry=list(item.geometry) if item.geometry is not None else None,
        parent_ids=tuple(item.parent_ids),
        conflict_ids=tuple(item.conflict_ids),
        score_features=dict(item.score_features),
        metadata=copy.deepcopy(dict(item.metadata)),
    )


def copy_layout(layout: LayoutResult, *, site: SiteSpec | None = None) -> LayoutResult:
    copied_site = site if site is not None else copy_site(layout.site)
    return replace(
        layout,
        site=copied_site,
        stalls=[copy_stall(stall) for stall in layout.stalls],
        aisles=[copy_aisle(aisle) for aisle in layout.aisles],
        attempts=list(layout.attempts),
        selected_branches=copy.deepcopy(list(layout.selected_branches)),
        selected_connectors=copy.deepcopy(list(layout.selected_connectors)),
        stall_type_attempts=copy.deepcopy(list(layout.stall_type_attempts)),
        stall_assignment_attempts=copy.deepcopy(list(layout.stall_assignment_attempts)),
        selected_stall_assignment=dict(layout.selected_stall_assignment),
        score=dict(layout.score),
        graph_validation=copy.deepcopy(dict(layout.graph_validation)),
        maneuver_validation=copy.deepcopy(dict(layout.maneuver_validation)),
        site_constraint_validation=copy.deepcopy(dict(layout.site_constraint_validation)),
        engineering_validation=copy.deepcopy(dict(layout.engineering_validation)),
        operational_quality=copy.deepcopy(dict(layout.operational_quality)),
        candidate_objects=[copy_candidate_object(item) for item in layout.candidate_objects],
        candidate_selection=copy.deepcopy(dict(layout.candidate_selection)),
        candidate_network_preview=copy.deepcopy(dict(layout.candidate_network_preview)),
        candidate_layout_preview=copy.deepcopy(dict(layout.candidate_layout_preview)),
        candidate_layout_promotion=copy.deepcopy(dict(layout.candidate_layout_promotion)),
        unsupported_phase1_inputs=copy.deepcopy(list(layout.unsupported_phase1_inputs)),
    )


def spine_family(layout: LayoutResult) -> str:
    mode = layout.generation_mode or ""
    if "multi_jog" in mode:
        return "multi_jog"
    if "dogleg" in mode:
        return "dogleg"
    jog_count = sum(1 for aisle in layout.aisles if aisle.role == "jog")
    if jog_count > 1:
        return "multi_jog"
    if jog_count == 1:
        return "dogleg"
    return "straight"


def spine_payload(layout: LayoutResult, source: dict[str, Any]) -> dict[str, Any]:
    aisles = [
        {
            "id": aisle.id,
            "role": aisle.role,
            "polygon": [list(point) for point in aisle.polygon],
            "parent_aisle_id": aisle.parent_aisle_id,
            "connected_to_entrance_id": aisle.connected_to_entrance_id,
            "directionality": aisle.directionality,
            "angle_degrees": aisle.angle_degrees,
        }
        for aisle in layout.aisles
        if aisle.role in SPINE_ROLES
    ]
    return {
        "family": source.get("family") or spine_family(layout),
        "entrance_id": layout.main_entrance_id,
        "exit_entrance_id": next(
            (aisle.connected_to_entrance_id for aisle in layout.aisles if aisle.role == "exit" and aisle.connected_to_entrance_id),
            None,
        ),
        "heading_degrees": layout.selected_heading_degrees,
        "heading_delta_degrees": layout.selected_heading_delta_degrees,
        "entrance_offset": layout.selected_entrance_offset,
        "aisle_lateral_offset": source.get("aisle_lateral_offset", 0.0),
        "dogleg_offset": source.get("dogleg_offset"),
        "aisles": aisles,
    }


def stall_assignment_payload(site: SiteSpec) -> dict[str, Any]:
    main = site.main_stall or site.stall
    branch = site.branch_stall or main
    return {"main": _canonicalize(main), "branch": _canonicalize(branch)}


def make_spine_id(payload: dict[str, Any]) -> str:
    return f"spine-{stable_digest(payload)[:16]}"


def make_candidate_id(spine_id: str, assignment: dict[str, Any]) -> str:
    return f"cand-{stable_digest({'spine_id': spine_id, 'stall_assignment': assignment})[:16]}"


def context_from_layout(
    layout: LayoutResult,
    *,
    source: dict[str, Any] | None = None,
    branch_diagnostics: list[dict[str, Any]] | None = None,
    collect_status: str = "collected",
    reject_reason: str | None = None,
) -> LayoutCandidateContext:
    isolated_site = copy_site(layout.site)
    isolated_layout = copy_layout(layout, site=isolated_site)
    record = dict(source or {})
    record.setdefault("family", spine_family(isolated_layout))
    payload = spine_payload(isolated_layout, record)
    spine_id = make_spine_id(payload)
    candidate_id = make_candidate_id(spine_id, stall_assignment_payload(isolated_site))
    isolated_layout = replace(
        isolated_layout,
        attempts=list(isolated_layout.attempts),
    )
    return LayoutCandidateContext(
        candidate_id=candidate_id,
        spine_id=spine_id,
        site=isolated_site,
        template_layout=isolated_layout,
        branch_diagnostics=copy.deepcopy(list(branch_diagnostics or [])),
        source=copy.deepcopy(record),
        collect_status=collect_status,
        reject_reason=reject_reason,
        spine_payload=payload,
    )


def contexts_are_same_spine(left: LayoutCandidateContext, right: LayoutCandidateContext) -> bool:
    if left.spine_id != right.spine_id:
        return False
    return left.spine_payload == right.spine_payload
