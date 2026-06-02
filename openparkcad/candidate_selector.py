from __future__ import annotations

from shapely.geometry import Polygon as ShapelyPolygon

from openparkcad.models import CandidateObject, SiteSpec


def select_candidate_objects(objects: list[CandidateObject], site: SiteSpec | None = None) -> dict[str, object]:
    eligible = [_selection_record(item) for item in objects if _eligible(item)]
    branch_records = sorted([item for item in eligible if item["role"] == "branch"], key=_selection_sort_key)
    connector_records = sorted([item for item in eligible if item["role"] == "connector"], key=_selection_sort_key)
    units = _selection_units(branch_records, connector_records)

    selected_ids: list[str] = []
    selected_set: set[str] = set()
    selected_branch_sources: set[str] = set()
    selected_branch_count = 0
    selected_bundles: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    max_branches = _max_branches(site)

    for unit in units:
        decision = _unit_rejection(unit, selected_set, selected_branch_sources, selected_branch_count, max_branches)
        if decision:
            continue
        _select_unit(unit, selected_ids, selected_set, selected_branch_sources)
        selected_branch_count += int(unit["branch_count"])
        if unit["type"] == "loop_bundle":
            selected_bundles.append(_bundle_report(unit))

    rejected = _rejected_records(branch_records, connector_records, selected_set, selected_branch_sources, selected_branch_count, max_branches)

    return {
        "version": "phase4c-3c",
        "strategy": "bundle_aware_shadow_aisle_skeleton_selector",
        "status": "shadow_only",
        "eligible_count": len(eligible),
        "eligible_branch_count": len(branch_records),
        "eligible_connector_count": len(connector_records),
        "eligible_bundle_count": len([unit for unit in units if unit["type"] == "loop_bundle"]),
        "selected_count": len(selected_ids),
        "selected_branch_count": selected_branch_count,
        "selected_connector_count": len([item for item in connector_records if str(item["id"]) in selected_set]),
        "selected_bundle_count": len(selected_bundles),
        "selected_bundles": selected_bundles,
        "max_branches": max_branches,
        "selected_ids": selected_ids,
        "selected_branch_source_ids": sorted(selected_branch_sources),
        "selected_score_total": sum(float(item["score"]) for item in eligible if str(item["id"]) in selected_set),
        "rejected": rejected,
        "notes": [
            "Selection is report-only and does not replace the generated layout yet.",
            "Loop bundles compete with single branch candidates before final selected ids are emitted.",
            "Branch selection respects optimization.max_branches.",
            "Connector candidates require both endpoint branch source ids to be selected by a bundle or by single branches.",
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
            "connector_inset_depth": candidate.metadata.get("connector_inset_depth"),
        },
    }


def _selection_sort_key(item: dict[str, object]) -> tuple[float, str]:
    return (-float(item["score"]), str(item["id"]))


def _selection_units(branch_records: list[dict[str, object]], connector_records: list[dict[str, object]]) -> list[dict[str, object]]:
    units = [_single_branch_unit(item) for item in branch_records]
    best_branch_by_source = _best_branch_by_source(branch_records)
    for connector in connector_records:
        connects = _connects(connector)
        if len(connects) != 2:
            continue
        branches = [best_branch_by_source.get(source_id) for source_id in connects]
        if not all(branches):
            continue
        branch_items = [branch for branch in branches if isinstance(branch, dict)]
        if len({_source_id(branch) for branch in branch_items}) != 2:
            continue
        if _internal_conflicts([*branch_items, connector]):
            continue
        units.append(_loop_bundle_unit(connector, branch_items))
    return sorted(units, key=lambda item: (-float(item["score"]), str(item["id"])))


def _single_branch_unit(branch: dict[str, object]) -> dict[str, object]:
    return {
        "id": f"UNIT-{branch['id']}",
        "type": "single_branch",
        "score": float(branch["score"]),
        "items": [branch],
        "branch_sources": [_source_id(branch)],
        "branch_count": 1,
        "connector_ids": [],
    }


def _loop_bundle_unit(connector: dict[str, object], branches: list[dict[str, object]]) -> dict[str, object]:
    branch_sources = [_source_id(branch) for branch in branches]
    loop_bonus = 150.0
    score = sum(float(branch["score"]) for branch in branches) + float(connector["score"]) + loop_bonus
    return {
        "id": f"UNIT-LOOP-{connector['id']}",
        "type": "loop_bundle",
        "score": score,
        "loop_bonus": loop_bonus,
        "items": [*branches, connector],
        "branch_sources": branch_sources,
        "branch_count": len(branch_sources),
        "connector_ids": [str(connector["id"])],
    }


def _best_branch_by_source(branch_records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    best: dict[str, dict[str, object]] = {}
    for branch in branch_records:
        source_id = _source_id(branch)
        if source_id not in best or float(branch["score"]) > float(best[source_id]["score"]):
            best[source_id] = branch
    return best


def _internal_conflicts(items: list[dict[str, object]]) -> bool:
    item_ids = {str(item["id"]) for item in items}
    for item in items:
        if item_ids.intersection(str(conflict_id) for conflict_id in item["conflict_ids"]):
            return True
    return False


def _unit_rejection(
    unit: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> dict[str, object] | None:
    score = float(unit["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    if selected_branch_count + int(unit["branch_count"]) > max_branches:
        return {"reason": "exceeds_max_branches", "conflicts_with": [], "max_branches": max_branches}
    duplicate_sources = sorted(set(_branch_sources(unit)).intersection(selected_branch_sources))
    if duplicate_sources:
        return {
            "reason": "duplicate_branch_source_selected",
            "conflicts_with": [],
            "branch_source_ids": duplicate_sources,
        }
    conflicts = sorted(set(_conflict_ids(unit)).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return None


def _select_unit(
    unit: dict[str, object],
    selected_ids: list[str],
    selected_set: set[str],
    selected_branch_sources: set[str],
) -> None:
    for item in unit["items"]:
        item_id = str(item["id"])
        if item_id in selected_set:
            continue
        selected_ids.append(item_id)
        selected_set.add(item_id)
        if item["role"] == "branch":
            selected_branch_sources.add(_source_id(item))


def _rejected_records(
    branch_records: list[dict[str, object]],
    connector_records: list[dict[str, object]],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> list[dict[str, object]]:
    rejected: list[dict[str, object]] = []
    for item in branch_records:
        if str(item["id"]) in selected_set:
            continue
        rejected.append({**item, **_branch_rejection_reason(item, selected_set, selected_branch_sources, selected_branch_count, max_branches)})
    for item in connector_records:
        if str(item["id"]) in selected_set:
            continue
        rejected.append({**item, **_connector_rejection_reason(item, selected_set, selected_branch_sources)})
    return rejected


def _branch_rejection_reason(
    item: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
    selected_branch_count: int,
    max_branches: int,
) -> dict[str, object]:
    score = float(item["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    source_id = _source_id(item)
    if source_id in selected_branch_sources:
        return {
            "reason": "duplicate_branch_source_selected",
            "conflicts_with": [],
            "branch_source_id": source_id,
        }
    if selected_branch_count >= max_branches:
        return {"reason": "exceeds_max_branches", "conflicts_with": [], "max_branches": max_branches}
    conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return {"reason": "not_selected_by_bundle_competition", "conflicts_with": []}


def _connector_rejection_reason(
    item: dict[str, object],
    selected_set: set[str],
    selected_branch_sources: set[str],
) -> dict[str, object]:
    score = float(item["score"])
    if score <= 0:
        return {"reason": "non_positive_shadow_score", "conflicts_with": []}
    missing = sorted(set(_connects(item)) - selected_branch_sources)
    if missing:
        return {
            "reason": "connector_dependency_not_selected",
            "conflicts_with": [],
            "missing_branch_source_ids": missing,
        }
    conflicts = sorted(set(item["conflict_ids"]).intersection(selected_set))
    if conflicts:
        return {"reason": "conflicts_with_selected_candidate", "conflicts_with": conflicts}
    return {"reason": "not_selected_by_bundle_competition", "conflicts_with": []}


def _bundle_report(unit: dict[str, object]) -> dict[str, object]:
    return {
        "id": unit["id"],
        "type": unit["type"],
        "score": unit["score"],
        "loop_bonus": unit.get("loop_bonus", 0.0),
        "selected_ids": [str(item["id"]) for item in unit["items"]],
        "branch_source_ids": _branch_sources(unit),
        "connector_ids": list(unit["connector_ids"]),
    }


def _branch_sources(unit: dict[str, object]) -> list[str]:
    return [str(item) for item in unit["branch_sources"]]


def _conflict_ids(unit: dict[str, object]) -> list[str]:
    conflicts: list[str] = []
    for item in unit["items"]:
        conflicts.extend(str(conflict_id) for conflict_id in item["conflict_ids"])
    return conflicts
    return None


def _source_id(item: dict[str, object]) -> str:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return str(item["id"])
    return str(metadata.get("source_id") or item["id"])


def _connects(item: dict[str, object]) -> list[str]:
    metadata = item.get("metadata", {})
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("connects", [])
    if not isinstance(raw, list | tuple):
        return []
    return [str(value) for value in raw]


def _max_branches(site: SiteSpec | None) -> int:
    if site is None:
        return 2
    raw = site.optimization.get("max_branches", 2)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 2


def _shadow_score(candidate: CandidateObject) -> float:
    stall_delta = _stall_delta(candidate)
    area_penalty = _geometry_area(candidate) * 0.2
    connector_bonus = 25.0 if candidate.role == "connector" and candidate.metadata.get("removed_turnarounds") else 0.0
    return stall_delta * 100.0 + connector_bonus - area_penalty


def _stall_delta(candidate: CandidateObject) -> float:
    if candidate.role == "connector":
        return float(candidate.score_features.get("added_stalls", 0.0))
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
