from __future__ import annotations

from dataclasses import replace

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.candidate_selector import select_candidate_objects
from openparkcad.models import CandidateObject, LayoutResult, ParkingAisle, ParkingStall, Polygon


def attach_candidate_snapshot(layout: LayoutResult) -> LayoutResult:
    objects = build_candidate_objects(layout)
    object.__setattr__(layout, "candidate_objects", objects)
    object.__setattr__(layout, "candidate_selection", select_candidate_objects(objects))
    return layout


def build_candidate_objects(layout: LayoutResult) -> list[CandidateObject]:
    objects: list[CandidateObject] = []
    objects.extend(_main_attempt_candidates(layout))
    objects.extend(_branch_and_connector_attempt_candidates(layout))
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
        "selection": layout.candidate_selection or select_candidate_objects(objects),
        "objects": [_candidate_report(item) for item in objects],
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
    for key in ("stall_count", "base_stall_count", "removed_stalls", "added_stalls", "length", "start_u"):
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
