from __future__ import annotations

from dataclasses import replace

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.candidate_layout_preview import build_candidate_layout_preview, candidate_layout_preview_layout
from openparkcad.candidate_network_preview import build_candidate_network_preview
from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.layout_geometry import available_area, normalized_local_box_to_world, polygon_points
from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle, ParkingStall, Polygon


def attach_candidate_snapshot(layout: LayoutResult) -> LayoutResult:
    objects = build_candidate_objects(layout)
    selection = select_candidate_objects(objects, layout.site)
    object.__setattr__(layout, "candidate_objects", objects)
    object.__setattr__(layout, "candidate_selection", selection)
    object.__setattr__(layout, "candidate_network_preview", build_candidate_network_preview(layout))
    object.__setattr__(layout, "candidate_layout_preview", build_candidate_layout_preview(layout))
    return _maybe_promote_candidate_layout(layout)


def build_candidate_objects(layout: LayoutResult) -> list[CandidateObject]:
    objects: list[CandidateObject] = []
    objects.extend(_main_attempt_candidates(layout))
    attempt_candidates = _branch_and_connector_attempt_candidates(layout)
    objects.extend(attempt_candidates)
    objects.extend(_synthetic_connector_candidates(layout, attempt_candidates))
    objects.extend(_selected_aisle_candidates(layout))
    objects.extend(_selected_stall_candidates(layout))
    return _with_conflicts(_deduplicate(objects))


def candidate_snapshot_report(layout: LayoutResult) -> dict[str, object]:
    objects = layout.candidate_objects or build_candidate_objects(layout)
    conflict_matrix = _conflict_matrix(objects)
    return {
        "version": "phase4b-1",
        "object_count": len(objects),
        "status_counts": _status_counts(objects),
        "conflict_count": len(conflict_matrix),
        "conflict_matrix": conflict_matrix,
        "selection": layout.candidate_selection or select_candidate_objects(objects, layout.site),
        "objects": [_candidate_report(item) for item in objects],
    }


def _maybe_promote_candidate_layout(layout: LayoutResult) -> LayoutResult:
    if not layout.site.optimization.get("promote_candidate_layout_preview", False):
        object.__setattr__(layout, "candidate_layout_promotion", _promotion_report(layout, "not_requested"))
        return layout

    comparison = layout.candidate_layout_preview.get("comparison", {})
    if not isinstance(comparison, dict) or not comparison.get("promotion_eligible"):
        object.__setattr__(layout, "candidate_layout_promotion", _promotion_report(layout, "rejected"))
        return layout

    preview_layout = candidate_layout_preview_layout(layout)
    validation = layout.candidate_layout_preview.get("validation", {})
    graph = validation.get("traffic_graph", {}) if isinstance(validation, dict) else {}
    maneuver = validation.get("maneuver_validation", {}) if isinstance(validation, dict) else {}
    promoted = replace(
        layout,
        aisles=preview_layout.aisles,
        stalls=preview_layout.stalls,
        generation_mode="candidate_layout_promoted",
        score=_preview_score(layout),
        graph_validation=graph if isinstance(graph, dict) else {},
        maneuver_validation=maneuver if isinstance(maneuver, dict) else {},
        candidate_layout_promotion=_promotion_report(layout, "promoted"),
    )
    return promoted


def _promotion_report(layout: LayoutResult, status: str) -> dict[str, object]:
    comparison = layout.candidate_layout_preview.get("comparison", {})
    if not isinstance(comparison, dict):
        comparison = {}
    blockers = comparison.get("promotion_blockers", [])
    if not isinstance(blockers, list):
        blockers = []
    return {
        "version": "phase4c-2b",
        "requested": bool(layout.site.optimization.get("promote_candidate_layout_preview", False)),
        "status": status,
        "source_preview_version": layout.candidate_layout_preview.get("version"),
        "promotion_eligible": bool(comparison.get("promotion_eligible", False)),
        "reason": _promotion_reason(status, comparison),
        "blockers": [str(item) for item in blockers],
        "official_output_replaced": status == "promoted",
    }


def _promotion_reason(status: str, comparison: dict[str, object]) -> str:
    if status == "not_requested":
        return "promote_candidate_layout_preview_not_enabled"
    if status == "promoted":
        return "candidate_layout_preview_promoted_to_official_output"
    reason = comparison.get("reason")
    return str(reason) if reason else "candidate_layout_preview_not_promotion_eligible"


def _preview_score(layout: LayoutResult) -> dict[str, float]:
    score = layout.candidate_layout_preview.get("score")
    if not isinstance(score, dict):
        return dict(layout.score)
    return {
        str(key): float(value)
        for key, value in score.items()
        if isinstance(value, int | float)
    }


def _main_attempt_candidates(layout: LayoutResult) -> list[CandidateObject]:
    candidates: list[CandidateObject] = []
    selected_key = (
        layout.main_entrance_id,
        layout.selected_heading_degrees,
        layout.selected_entrance_offset,
    )
    for index, attempt in enumerate(layout.attempts, start=1):
        key = (
            attempt.entrance_id,
            attempt.angle_degrees,
            attempt.entrance_offset,
        )
        selected = _same_main_attempt(key, selected_key)
        status = "selected" if selected else ("rejected" if attempt.graph_valid else "invalid")
        candidates.append(
            CandidateObject(
                id=f"C-MAIN-{index:03d}",
                kind="aisle_skeleton",
                role="main",
                status=status,
                score_features={
                    "stall_count": float(attempt.stall_count),
                    "heading_delta_degrees": float(attempt.heading_delta_degrees),
                    "entrance_offset": float(attempt.entrance_offset),
                },
                metadata={
                    "entrance_id": attempt.entrance_id,
                    "heading_degrees": attempt.angle_degrees,
                    "heading_delta_degrees": attempt.heading_delta_degrees,
                    "entrance_offset": attempt.entrance_offset,
                    "branch_side": attempt.branch_side,
                    "branch_start_u": attempt.branch_start_u,
                    "branch_length": attempt.branch_length,
                    "graph_valid": attempt.graph_valid,
                    "graph_errors": attempt.graph_errors,
                },
            )
        )
    return candidates


def _branch_and_connector_attempt_candidates(layout: LayoutResult) -> list[CandidateObject]:
    candidates: list[CandidateObject] = []
    sequence = 1
    for attempt in layout.attempts:
        for diagnostic in attempt.branch_candidates:
            source_id = str(diagnostic.get("branch_id") or diagnostic.get("connector_id") or "")
            if not source_id:
                continue
            is_connector = "connector_id" in diagnostic
            candidate_id = f"{source_id}-TRY-{sequence:03d}"
            selected = (
                _connector_attempt_selected(diagnostic, layout.selected_connectors)
                if is_connector
                else _branch_attempt_selected(diagnostic, layout.selected_branches)
            )
            candidates.append(_attempt_candidate(candidate_id, source_id, diagnostic, selected, is_connector))
            sequence += 1
    return candidates


def _attempt_candidate(
    candidate_id: str,
    source_id: str,
    diagnostic: dict[str, object],
    selected: bool,
    is_connector: bool,
) -> CandidateObject:
    reason = str(diagnostic.get("reason", "candidate_evaluated"))
    graph_valid = bool(diagnostic.get("graph_valid", True))
    status = "selected" if selected else ("rejected" if graph_valid else "invalid")
    role = "connector" if is_connector else "branch"
    parent_ids = tuple(str(item) for item in diagnostic.get("connects", [])) if is_connector else ("A-MAIN",)
    score_features: dict[str, float] = {}
    for key in ("stall_count", "base_stall_count", "removed_stalls", "added_stalls", "length", "start_u", "connector_inset_depth"):
        value = diagnostic.get(key)
        if isinstance(value, int | float):
            score_features[key] = float(value)
    return CandidateObject(
        id=f"C-{candidate_id}",
        kind="aisle_skeleton",
        role=role,
        status=status,
        geometry=_diagnostic_geometry(diagnostic),
        parent_ids=parent_ids,
        score_features=score_features,
        metadata={
            **diagnostic,
            "source_id": source_id,
            "accepted_reason": reason if selected else None,
        },
    )


def _synthetic_connector_candidates(layout: LayoutResult, candidates: list[CandidateObject]) -> list[CandidateObject]:
    if not layout.site.optimization.get("enable_connectors", True):
        return []
    entrance = next((item for item in layout.site.entrances if item.id == layout.main_entrance_id), None)
    if entrance is None:
        return []
    heading = layout.selected_heading_degrees
    if heading is None:
        return []
    branches = _best_shadow_branches_by_source(candidates)
    existing_keys = _existing_connector_keys(candidates)
    synthetic: list[CandidateObject] = []
    for left, right in _synthetic_connector_pairs(branches):
        connects = tuple(sorted((_source_id(left), _source_id(right))))
        if connects in existing_keys:
            continue
        for inset_depth in _connector_inset_depths(layout.site, left, right):
            connector_result = _synthetic_connector_geometry(layout, entrance, heading, left, right, inset_depth)
            if connector_result is None:
                continue
            connector, actual_inset_depth = connector_result
            status = "rejected"
            if not available_area(layout.site).covers(ShapelyPolygon(connector)):
                status = "invalid"
            sequence = len(synthetic) + 1
            synthetic.append(
                CandidateObject(
                    id=f"C-SHADOW-CONNECTOR-{sequence:03d}",
                    kind="aisle_skeleton",
                    role="connector",
                    status=status,
                    geometry=connector,
                    parent_ids=(connects[0], connects[1]),
                    score_features={
                        "added_stalls": 0.0,
                        "removed_stalls": 0.0,
                        "length": abs(_start_u(left) - _start_u(right)),
                        "connector_inset_depth": actual_inset_depth,
                    },
                    metadata={
                        "source_id": f"A-SHADOW-CONNECTOR-{sequence:03d}",
                        "reason": "shadow_connector_synthesized" if status != "invalid" else "shadow_connector_outside_usable_area",
                        "connects": list(connects),
                        "side": _side(left),
                        "start_u_min": min(_start_u(left), _start_u(right)),
                        "start_u_max": max(_start_u(left), _start_u(right)),
                        "length": abs(_start_u(left) - _start_u(right)),
                        "connector_inset_depth": actual_inset_depth,
                        "removed_turnarounds": [f"{connects[0]}-TURNAROUND", f"{connects[1]}-TURNAROUND"],
                        "generated_stalls": [],
                        "synthetic": True,
                    },
                )
            )
    return synthetic


def _best_shadow_branches_by_source(candidates: list[CandidateObject]) -> list[CandidateObject]:
    best: dict[str, CandidateObject] = {}
    for candidate in candidates:
        if candidate.role != "branch" or candidate.geometry is None or candidate.status == "invalid":
            continue
        if not _branch_metadata_complete(candidate):
            continue
        source = _source_id(candidate)
        if source not in best or _branch_candidate_score(candidate) > _branch_candidate_score(best[source]):
            best[source] = candidate
    return sorted(best.values(), key=lambda item: (_side(item), _start_u(item), _source_id(item)))


def _synthetic_connector_pairs(branches: list[CandidateObject]) -> list[tuple[CandidateObject, CandidateObject]]:
    pairs: list[tuple[CandidateObject, CandidateObject]] = []
    by_side: dict[str, list[CandidateObject]] = {}
    for branch in branches:
        by_side.setdefault(_side(branch), []).append(branch)
    for side_branches in by_side.values():
        ordered = sorted(side_branches, key=_start_u)
        pairs.extend(zip(ordered, ordered[1:]))
    return pairs


def _synthetic_connector_geometry(
    layout: LayoutResult,
    entrance,
    heading_degrees: float,
    left: CandidateObject,
    right: CandidateObject,
    inset_depth: float,
) -> tuple[Polygon, float] | None:
    u1 = _start_u(left)
    u2 = _start_u(right)
    if abs(u2 - u1) <= layout.site.aisle_width:
        return None
    if _side(left) != _side(right):
        return None
    direction = 1 if _side(left) == "left" else -1
    length = min(_branch_length(left), _branch_length(right))
    if length <= layout.site.aisle_width:
        return None
    actual_inset_depth = min(max(float(inset_depth), 0.0), _max_connector_inset_depth(layout.site, length))
    center_v = direction * (length - layout.site.aisle_width / 2 - actual_inset_depth)
    u_min = min(u1, u2) - layout.site.aisle_width / 2
    u_max = max(u1, u2) + layout.site.aisle_width / 2
    local = (
        u_min,
        center_v - layout.site.aisle_width / 2,
        u_max,
        center_v + layout.site.aisle_width / 2,
    )
    return polygon_points(normalized_local_box_to_world(local, entrance, heading_degrees)), actual_inset_depth


def _connector_inset_depths(site, left: CandidateObject, right: CandidateObject) -> tuple[float, ...]:
    branch_length = min(_branch_length(left), _branch_length(right))
    max_depth = _max_connector_inset_depth(site, branch_length)
    raw = site.optimization.get("connector_inset_depths")
    if isinstance(raw, list):
        depths = []
        for item in raw:
            try:
                depths.append(float(item))
            except (TypeError, ValueError):
                continue
        return _normalized_inset_depths(depths, max_depth)
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return (0.0,)
    stall = site.branch_stall or site.main_stall or site.stall
    if stall.family != "perpendicular" or not _angle_allowed(90.0, stall.allowed_angles):
        return (0.0,)
    return _normalized_inset_depths(
        [0.0, stall.length / 2, stall.length, stall.length * 1.5],
        max_depth,
    )


def _normalized_inset_depths(depths: list[float], max_depth: float) -> tuple[float, ...]:
    values = {round(min(max(depth, 0.0), max_depth), 6) for depth in depths}
    values.add(0.0)
    return tuple(sorted(values))


def _max_connector_inset_depth(site, branch_length: float) -> float:
    return max(branch_length - site.aisle_width * 2, 0.0)


def _angle_allowed(angle: float, allowed_angles) -> bool:
    return any(abs(float(item) - angle) <= 1e-6 for item in allowed_angles)


def _connector_outer_stall_depth(site, branch_length: float) -> float:
    if not site.optimization.get("connector_allow_outer_stall_row", True):
        return 0.0
    stall = site.branch_stall or site.main_stall or site.stall
    if stall.family != "perpendicular" or not _angle_allowed(90.0, stall.allowed_angles):
        return 0.0
    return min(stall.length, _max_connector_inset_depth(site, branch_length))


def _branch_metadata_complete(candidate: CandidateObject) -> bool:
    return (
        isinstance(candidate.metadata.get("source_id"), str)
        and isinstance(candidate.metadata.get("side"), str)
        and isinstance(candidate.metadata.get("start_u"), int | float)
        and isinstance(candidate.metadata.get("length"), int | float)
    )


def _branch_candidate_score(candidate: CandidateObject) -> float:
    stall_count = candidate.score_features.get("stall_count")
    base_stall_count = candidate.score_features.get("base_stall_count")
    if isinstance(stall_count, int | float) and isinstance(base_stall_count, int | float):
        return float(stall_count) - float(base_stall_count)
    return float(candidate.score_features.get("length", 0.0))


def _existing_connector_keys(candidates: list[CandidateObject]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.role != "connector":
            continue
        connects = candidate.metadata.get("connects", [])
        if not isinstance(connects, list | tuple) or len(connects) != 2:
            continue
        keys.add(tuple(sorted(str(item) for item in connects)))
    return keys


def _selected_aisle_candidates(layout: LayoutResult) -> list[CandidateObject]:
    return [_aisle_candidate(aisle) for aisle in layout.aisles]


def _selected_stall_candidates(layout: LayoutResult) -> list[CandidateObject]:
    return [_stall_candidate(stall) for stall in layout.stalls]


def _aisle_candidate(aisle: ParkingAisle) -> CandidateObject:
    return CandidateObject(
        id=f"C-SELECTED-{aisle.id}",
        kind="aisle",
        role=aisle.role,
        status="selected",
        geometry=aisle.polygon,
        parent_ids=tuple(item for item in (aisle.parent_aisle_id, aisle.connected_to_entrance_id) if item),
        score_features={"area": _area(aisle.polygon)},
        metadata={
            "source_id": aisle.id,
            "angle_degrees": aisle.angle_degrees,
            "connected_to_entrance_id": aisle.connected_to_entrance_id,
            "parent_aisle_id": aisle.parent_aisle_id,
            "connected_aisle_ids": list(aisle.connected_aisle_ids),
        },
    )


def _stall_candidate(stall: ParkingStall) -> CandidateObject:
    return CandidateObject(
        id=f"C-SELECTED-{stall.id}",
        kind="stall",
        role="stall",
        status="selected",
        geometry=stall.polygon,
        parent_ids=(stall.served_by_aisle_id,) if stall.served_by_aisle_id else (),
        score_features={"area": _area(stall.polygon)},
        metadata={
            "source_id": stall.id,
            "angle_degrees": stall.angle_degrees,
            "aisle_side": stall.aisle_side,
            "stall_type_id": stall.stall_type_id,
        },
    )


def _candidate_report(candidate: CandidateObject) -> dict[str, object]:
    return {
        "id": candidate.id,
        "kind": candidate.kind,
        "role": candidate.role,
        "status": candidate.status,
        "geometry": candidate.geometry,
        "parent_ids": list(candidate.parent_ids),
        "conflict_ids": list(candidate.conflict_ids),
        "score_features": candidate.score_features,
        "metadata": candidate.metadata,
    }


def _status_counts(objects: list[CandidateObject]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in objects:
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def _with_conflicts(objects: list[CandidateObject]) -> list[CandidateObject]:
    conflict_map: dict[str, set[str]] = {item.id: set() for item in objects}
    for index, left in enumerate(objects):
        for right in objects[index + 1 :]:
            conflict = _geometry_conflict(left, right)
            if conflict is None:
                continue
            conflict_map[left.id].add(right.id)
            conflict_map[right.id].add(left.id)
    return [
        replace(item, conflict_ids=tuple(sorted(conflict_map[item.id])))
        for item in objects
    ]


def _conflict_matrix(objects: list[CandidateObject]) -> list[dict[str, object]]:
    conflicts: list[dict[str, object]] = []
    for index, left in enumerate(objects):
        for right in objects[index + 1 :]:
            conflict = _geometry_conflict(left, right)
            if conflict is None:
                continue
            conflicts.append(conflict)
    return conflicts


def _geometry_conflict(left: CandidateObject, right: CandidateObject) -> dict[str, object] | None:
    if left.geometry is None or right.geometry is None:
        return None
    if _allowed_overlap(left, right):
        return None
    left_geometry = ShapelyPolygon(left.geometry)
    right_geometry = ShapelyPolygon(right.geometry)
    intersection = left_geometry.intersection(right_geometry)
    if intersection.area <= 1e-6:
        return None
    return {
        "left_id": left.id,
        "right_id": right.id,
        "type": "geometry_overlap",
        "overlap_area": float(intersection.area),
    }


def _allowed_overlap(left: CandidateObject, right: CandidateObject) -> bool:
    if not _is_aisle_like(left) or not _is_aisle_like(right):
        return False
    left_source = _source_id(left)
    right_source = _source_id(right)
    if left_source in right.parent_ids or right_source in left.parent_ids:
        return True
    if left_source in _connected_source_ids(right) or right_source in _connected_source_ids(left):
        return True
    return False


def _is_aisle_like(candidate: CandidateObject) -> bool:
    return candidate.kind in {"aisle", "aisle_skeleton"}


def _same_main_attempt(
    left: tuple[str | None, float, float],
    right: tuple[str | None, float | None, float],
) -> bool:
    return (
        left[0] == right[0]
        and right[1] is not None
        and abs(left[1] - right[1]) <= 1e-6
        and abs(left[2] - right[2]) <= 1e-6
    )


def _branch_attempt_selected(diagnostic: dict[str, object], selected_branches: list[dict[str, object]]) -> bool:
    for branch in selected_branches:
        if str(diagnostic.get("branch_id")) != str(branch.get("id")):
            continue
        if str(diagnostic.get("side")) != str(branch.get("side")):
            continue
        if not _close(diagnostic.get("start_u"), branch.get("start_u")):
            continue
        if not _close(diagnostic.get("length"), branch.get("length")):
            continue
        return True
    return False


def _connector_attempt_selected(diagnostic: dict[str, object], selected_connectors: list[dict[str, object]]) -> bool:
    diagnostic_connects = [str(item) for item in diagnostic.get("connects", [])]
    for connector in selected_connectors:
        if str(diagnostic.get("connector_id")) != str(connector.get("id")):
            continue
        if diagnostic_connects == [str(item) for item in connector.get("connects", [])]:
            diagnostic_inset = diagnostic.get("connector_inset_depth")
            selected_inset = connector.get("connector_inset_depth")
            if diagnostic_inset is None and selected_inset is None:
                return True
            if _close(diagnostic_inset, selected_inset):
                return True
    return False


def _close(left: object, right: object) -> bool:
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        return False
    return abs(float(left) - float(right)) <= 1e-6


def _diagnostic_geometry(diagnostic: dict[str, object]) -> Polygon | None:
    raw = diagnostic.get("geometry")
    if not isinstance(raw, list):
        return None
    points: Polygon = []
    for item in raw:
        if not isinstance(item, list | tuple) or len(item) != 2:
            return None
        points.append((float(item[0]), float(item[1])))
    return points if len(points) >= 3 else None


def _source_id(candidate: CandidateObject) -> str:
    source_id = candidate.metadata.get("source_id")
    return str(source_id) if source_id is not None else candidate.id


def _side(candidate: CandidateObject) -> str:
    return str(candidate.metadata.get("side", ""))


def _start_u(candidate: CandidateObject) -> float:
    value = candidate.metadata.get("start_u")
    return float(value) if isinstance(value, int | float) else 0.0


def _branch_length(candidate: CandidateObject) -> float:
    value = candidate.metadata.get("length")
    return float(value) if isinstance(value, int | float) else 0.0


def _connected_source_ids(candidate: CandidateObject) -> tuple[str, ...]:
    raw = candidate.metadata.get("connected_aisle_ids", [])
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(str(item) for item in raw)


def _area(polygon: Polygon) -> float:
    return float(ShapelyPolygon(polygon).area)


def _deduplicate(objects: list[CandidateObject]) -> list[CandidateObject]:
    unique: list[CandidateObject] = []
    seen: set[str] = set()
    for item in objects:
        if item.id in seen:
            continue
        unique.append(item)
        seen.add(item.id)
    return unique
