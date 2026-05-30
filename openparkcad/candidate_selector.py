from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import CandidateObject


def select_candidate_objects(objects: list[CandidateObject]) -> dict[str, object]:
    eligible = [_selection_record(item) for item in objects if _eligible(item)]
    eligible.sort(key=lambda item: (-float(item["score"]), str(item["id"])))

    selected_ids: list[str] = []
    selected_set: set[str] = set()
    rejected: list[dict[str, object]] = []

    for item in eligible:
        candidate_id = str(item["id"])
        score = float(item["score"])
        if score <= 0:
            rejected.append({**item, "reason": "non_positive_shadow_score", "conflicts_with": []})
            continue
        conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
        if conflicts:
            rejected.append({**item, "reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts})
            continue
        selected_ids.append(candidate_id)
        selected_set.add(candidate_id)

    return {
        "version": "phase4b-1",
        "strategy": "greedy_shadow_aisle_skeleton_selector",
        "status": "shadow_only",
        "eligible_count": len(eligible),
        "selected_count": len(selected_ids),
        "selected_ids": selected_ids,
        "selected_score_total": sum(float(item["score"]) for item in eligible if str(item["id"]) in selected_set),
        "rejected": rejected,
        "notes": [
            "Selection is report-only and does not replace the generated layout yet.",
            "Only branch and connector aisle-skeleton candidates are selected in this phase.",
            "Selected stalls are intentionally not treated as fixed blockers because a real optimizer would regenerate stalls around the selected road network.",
        ],
    }


def _eligible(candidate: CandidateObject) -> bool:
    return (
        candidate.kind == "aisle_skeleton"
        and candidate.role in {"branch", "connector"}
        and candidate.geometry is not None
        and candidate.status != "invalid"
    )


def _selection_record(candidate: CandidateObject) -> dict[str, object]:
    return {
        "id": candidate.id,
        "role": candidate.role,
        "status": candidate.status,
        "score": _shadow_score(candidate),
        "conflict_ids": list(candidate.conflict_ids),
        "score_features": candidate.score_features,
        "metadata": {
            "source_id": candidate.metadata.get("source_id"),
            "reason": candidate.metadata.get("reason"),
            "side": candidate.metadata.get("side"),
            "start_u": candidate.metadata.get("start_u"),
            "length": candidate.metadata.get("length"),
            "connects": candidate.metadata.get("connects"),
        },
    }


def _shadow_score(candidate: CandidateObject) -> float:
    stall_delta = _stall_delta(candidate)
    area_penalty = _geometry_area(candidate) * 0.2
    connector_bonus = 25.0 if candidate.role == "connector" and candidate.metadata.get("removed_turnarounds") else 0.0
    return stall_delta * 100.0 + connector_bonus - area_penalty


def _stall_delta(candidate: CandidateObject) -> float:
    stall_count = candidate.score_features.get("stall_count")
    base_stall_count = candidate.score_features.get("base_stall_count")
    if stall_count is not None and base_stall_count is not None:
        return float(stall_count) - float(base_stall_count)
    added = float(candidate.score_features.get("added_stalls", 0.0))
    removed = float(candidate.score_features.get("removed_stalls", 0.0))
    return added - removed


def _geometry_area(candidate: CandidateObject) -> float:
    if candidate.geometry is None:
        return 0.0
    return float(ShapelyPolygon(candidate.geometry).area)
