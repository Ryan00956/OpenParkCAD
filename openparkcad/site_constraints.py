from __future__ import annotations

from dataclasses import dataclass
from re import split
from typing import Any, Literal

from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openparkcad.models import LayoutResult, SiteAreaSpec, SiteSpec, StallSpec

ConstraintPurpose = Literal["all", "stall", "aisle", "swept_path"]
_CONCRETE_PURPOSES = frozenset({"stall", "aisle", "swept_path"})
_ADVISORY_PRIORITIES = frozenset({"advisory", "draw_only", "future", "soft"})
_AUTHORITY_TIERS = ("advisory", "project_policy", "jurisdictional")
_AREA_TOLERANCE = 1e-6


@dataclass(frozen=True)
class ConstraintGeometry:
    id: str
    source: str
    kind: str
    base_geometry: BaseGeometry
    exclusion_geometry: BaseGeometry
    clearance: float
    purposes: frozenset[str]
    affects: tuple[str, ...] = ()
    authority: str = "project_policy"
    priority: str = "hard"
    required: bool = False

    @property
    def hard(self) -> bool:
        return (
            self.authority.strip().lower() != "advisory"
            and self.priority.strip().lower() not in _ADVISORY_PRIORITIES
        )


def declared_constraint_geometries(
    site: SiteSpec,
    *,
    include_advisory: bool = False,
) -> list[ConstraintGeometry]:
    """Normalize input declarations into deterministic hard-exclusion geometry.

    Purposes are explicit: ``stall`` is marked-space placement, ``aisle`` is
    ordinary circulation geometry, and ``swept_path`` is vehicle maneuver
    clearance. ``all`` is accepted by query helpers and means the union of all
    three scopes.
    """

    declarations: list[ConstraintGeometry] = []
    declarations.extend(_obstacle_geometries(site))
    declarations.extend(_reserved_area_geometries(site))
    declarations.extend(_site_feature_geometries(site))
    declarations.extend(_route_geometries(site))
    if include_advisory:
        return declarations
    return [item for item in declarations if item.hard]


def site_exclusion_geometry(site: SiteSpec, purpose: ConstraintPurpose = "all") -> BaseGeometry:
    purpose = _normalize_purpose(purpose)
    geometries = [
        item.exclusion_geometry
        for item in declared_constraint_geometries(site)
        if _matches_purpose(item, purpose)
    ]
    return unary_union(geometries) if geometries else GeometryCollection()


def site_usable_area(site: SiteSpec, purpose: ConstraintPurpose = "all") -> BaseGeometry:
    boundary = _valid_polygon(site.boundary, "site.boundary")
    usable = boundary.buffer(-site.margin, join_style="mitre")
    exclusions = site_exclusion_geometry(site, purpose)
    return usable.difference(exclusions) if not exclusions.is_empty else usable


def constraint_conflicts(
    site: SiteSpec,
    geometry: BaseGeometry,
    purpose: ConstraintPurpose,
) -> list[dict[str, Any]]:
    purpose = _normalize_purpose(purpose)
    conflicts: list[dict[str, Any]] = []
    for item in declared_constraint_geometries(site):
        if not _matches_purpose(item, purpose):
            continue
        overlap_area = geometry.intersection(item.exclusion_geometry).area
        if overlap_area <= _AREA_TOLERANCE:
            continue
        conflicts.append(
            {
                "constraint_id": item.id,
                "source": item.source,
                "kind": item.kind,
                "purpose": purpose,
                "overlap_area": float(overlap_area),
                "clearance": item.clearance,
                "authority": item.authority,
                "priority": item.priority,
            }
        )
    return conflicts


def validate_site_constraints(layout: LayoutResult) -> dict[str, Any]:
    errors: list[str] = []
    definition_report = validate_site_constraint_definitions(layout.site)
    errors.extend(definition_report["errors"])

    stall_conflicts = _layout_object_conflicts(layout, "stall") if definition_report["valid"] else []
    aisle_conflicts = _layout_object_conflicts(layout, "aisle") if definition_report["valid"] else []
    for conflict in [*stall_conflicts, *aisle_conflicts]:
        errors.append(str(conflict["message"]))

    quota_report = validate_parking_quotas(layout)
    errors.extend(str(item) for item in quota_report["errors"])
    active_constraints: list[ConstraintGeometry] = []
    all_constraints: list[ConstraintGeometry] = []
    if definition_report["valid"]:
        active_constraints = declared_constraint_geometries(layout.site)
        all_constraints = declared_constraint_geometries(layout.site, include_advisory=True)
    else:
        try:
            all_constraints = declared_constraint_geometries(layout.site, include_advisory=True)
        except ValueError:
            pass

    return {
        "version": "v0.3-site-constraints",
        "valid": not errors,
        "errors": errors,
        "scope": {
            "stall": "marked stall footprints",
            "aisle": "ordinary vehicle circulation footprints",
            "swept_path": "vehicle maneuver and swept-path footprints",
        },
        "active_constraint_count": len(active_constraints),
        "active_constraint_ids": [item.id for item in active_constraints],
        "required_constraint_ids": [item.id for item in all_constraints if item.required],
        "authority": _authority_summary(layout.site, all_constraints),
        "definition_validation": definition_report,
        "conflicts": {
            "stalls": stall_conflicts,
            "aisles": aisle_conflicts,
        },
        "quota": quota_report,
    }


def validate_site_constraint_definitions(site: SiteSpec) -> dict[str, Any]:
    errors: list[str] = []
    boundary: BaseGeometry | None = None
    try:
        boundary = _valid_polygon(site.boundary, "site.boundary")
    except ValueError as exc:
        errors.append(str(exc))

    declarations: list[ConstraintGeometry] = []
    try:
        declarations = declared_constraint_geometries(site, include_advisory=True)
    except ValueError as exc:
        errors.append(str(exc))

    if boundary is not None:
        for item in declarations:
            if not item.hard:
                continue
            if not boundary.buffer(_AREA_TOLERANCE).covers(item.base_geometry):
                errors.append(
                    f"{item.source} '{item.id}' extends outside the site boundary"
                )

    invalid_authorities = sorted(
        {
            item.authority
            for item in declarations
            if item.authority.strip().lower() not in _AUTHORITY_TIERS
        }
    )
    if invalid_authorities:
        errors.append(
            "unsupported constraint authority: " + ", ".join(invalid_authorities)
        )
    if any(item.authority.strip().lower() == "jurisdictional" for item in declarations):
        metadata = _jurisdictional_metadata_status(site)
        if not metadata["valid"]:
            errors.append(
                "jurisdictional constraints need standards metadata: "
                + ", ".join(metadata["missing"])
            )

    pedestrian_data = site.pedestrian_and_emergency
    errors.extend(_missing_required_route_geometry_errors(pedestrian_data))
    if pedestrian_data.get("emergency_access_required", False) and not _has_hard_route_geometry(
        pedestrian_data,
        ("fire_lanes", "access_routes", "emergency_access_routes"),
    ):
        errors.append(
            "pedestrian_and_emergency.emergency_access_required needs a hard fire/access route geometry"
        )
    if site.parking_quotas.get("accessible_min", 0) > 0 and not _has_hard_route_geometry(
        pedestrian_data,
        ("accessible_routes",),
    ):
        errors.append("parking.quotas.accessible_min needs a hard accessible route geometry")

    return {
        "valid": not errors,
        "errors": errors,
        "declared_count": len(declarations),
        "hard_count": sum(1 for item in declarations if item.hard),
        "authority": _authority_summary(site, declarations),
    }


def validate_parking_quotas(layout: LayoutResult) -> dict[str, Any]:
    requested = {
        "accessible_min": int(layout.site.parking_quotas.get("accessible_min", 0)),
        "ev_min": int(layout.site.parking_quotas.get("ev_min", 0)),
    }
    recognized_keys = set(requested)
    errors = [
        f"parking quota '{key}' is not enforceable in v0.3"
        for key, value in layout.site.parking_quotas.items()
        if key not in recognized_keys and value > 0
    ]

    actual = {"accessible": 0, "ev": 0}
    matching_stall_ids: dict[str, list[str]] = {"accessible": [], "ev": []}
    specs = _stall_specs_by_id(layout.site)
    for stall in layout.stalls:
        stall_type_id = stall.stall_type_id or layout.site.stall.id
        spec = specs.get(stall_type_id)
        capabilities = stall_type_capabilities(spec) if spec else frozenset()
        for capability in actual:
            if capability in capabilities:
                actual[capability] += 1
                matching_stall_ids[capability].append(stall.id)

    shortfall = {
        "accessible": max(0, requested["accessible_min"] - actual["accessible"]),
        "ev": max(0, requested["ev_min"] - actual["ev"]),
    }
    if shortfall["accessible"]:
        errors.append(
            "accessible parking quota requires "
            f"{requested['accessible_min']} stalls, layout provides {actual['accessible']}"
        )
    if shortfall["ev"]:
        errors.append(
            f"EV parking quota requires {requested['ev_min']} stalls, layout provides {actual['ev']}"
        )

    return {
        "valid": not errors,
        "required": requested,
        "actual": actual,
        "shortfall": shortfall,
        "matching_stall_ids": matching_stall_ids,
        "errors": errors,
        "assignment_mode": "count_explicit_stall_type_classifications",
    }


def stall_type_capabilities(spec: StallSpec | None) -> frozenset[str]:
    if spec is None:
        return frozenset()

    tokens = set(spec.classifications)
    tokens.update(token for token in split(r"[^a-z0-9]+", spec.id.lower()) if token)
    tokens.update(token for token in split(r"[^a-z0-9]+", spec.family.lower()) if token)
    feature_types = {
        str(feature.get("type", "")).strip().lower()
        for feature in spec.fixed_features
    }
    capabilities: set[str] = set()
    if tokens.intersection({"accessible", "ada", "disabled", "wheelchair"}):
        capabilities.add("accessible")
    if tokens.intersection({"ev", "electric", "charging", "charger"}) or feature_types.intersection(
        {"charging_post", "ev_charger", "charger"}
    ):
        capabilities.add("ev")
    return frozenset(capabilities)


def _obstacle_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    specs = site.obstacle_specs
    if not specs:
        default_clearance = float(site.constraints.get("setbacks", {}).get("obstacle", 0.0))
        specs = tuple(
            SiteAreaSpec(
                id=f"obstacle-{index}",
                kind="obstacle",
                geometry={"type": "polygon", "points": polygon},
                clearance=default_clearance,
            )
            for index, polygon in enumerate(site.obstacles, start=1)
        )
    return [
        _constraint_from_area(spec, "obstacle", _CONCRETE_PURPOSES)
        for spec in specs
    ]


def _reserved_area_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    for spec in site.reserved_areas:
        purposes = _purposes_from_affects(spec.affects)
        if not spec.affects:
            purposes = _purposes_from_access_flags(
                parking_allowed=spec.parking_allowed,
                vehicle_allowed=spec.vehicle_allowed,
            )
        geometries.append(_constraint_from_area(spec, "reserved_area", frozenset(purposes)))
    return geometries


def _site_feature_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    for index, feature in enumerate(site.site_features, start=1):
        if not isinstance(feature, dict):
            raise ValueError(f"site_features[{index}] must be an object")
        if not feature.get("enabled", True):
            continue
        affects = tuple(str(value) for value in feature.get("affects", []))
        purposes = _purposes_from_affects(affects)
        if feature.get("forbidden", False):
            purposes.update(_CONCRETE_PURPOSES)
        if not purposes:
            continue
        geometry = _declared_geometry(feature.get("geometry"), f"site_features[{index}].geometry")
        clearance = _nonnegative_float(feature.get("clearance", 0.0), f"site_features[{index}].clearance")
        geometries.append(
            _make_constraint(
                constraint_id=str(feature.get("id", f"site-feature-{index}")),
                source="site_feature",
                kind=str(feature.get("type", "feature")),
                geometry=geometry,
                clearance=clearance,
                purposes=frozenset(purposes),
                affects=affects,
                authority=str(feature.get("authority", "project_policy")),
                priority=str(feature.get("priority", "hard")),
                required=bool(feature.get("required", False)),
            )
        )
    return geometries


def _route_geometries(site: SiteSpec) -> list[ConstraintGeometry]:
    geometries: list[ConstraintGeometry] = []
    route_defaults = {
        "pedestrian_routes": (False, False),
        "accessible_routes": (False, False),
        "fire_lanes": (False, True),
        "access_routes": (False, True),
        "emergency_access_routes": (False, True),
    }
    for key, defaults in route_defaults.items():
        raw_items = site.pedestrian_and_emergency.get(key, [])
        if not isinstance(raw_items, list):
            raise ValueError(f"pedestrian_and_emergency.{key} must be an array")
        for index, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"pedestrian_and_emergency.{key}[{index}] must be an object")
            if not item.get("enabled", True) or "geometry" not in item:
                continue
            affects = tuple(str(value) for value in item.get("affects", []))
            if affects:
                purposes = _purposes_from_affects(affects)
                if "parking_allowed" in item:
                    if item["parking_allowed"]:
                        purposes.discard("stall")
                    else:
                        purposes.add("stall")
                if "vehicle_allowed" in item:
                    if item["vehicle_allowed"]:
                        purposes.difference_update({"aisle", "swept_path"})
                    else:
                        purposes.update({"aisle", "swept_path"})
            else:
                parking_allowed = bool(item.get("parking_allowed", defaults[0]))
                vehicle_allowed = bool(item.get("vehicle_allowed", defaults[1]))
                purposes = _purposes_from_access_flags(parking_allowed, vehicle_allowed)
            label = f"pedestrian_and_emergency.{key}[{index}]"
            geometry = _declared_geometry(item["geometry"], f"{label}.geometry")
            clearance = _nonnegative_float(item.get("clearance", 0.0), f"{label}.clearance")
            geometries.append(
                _make_constraint(
                    constraint_id=str(item.get("id", f"{key}-{index}")),
                    source=key.removesuffix("s"),
                    kind=str(item.get("type", key.removesuffix("s"))),
                    geometry=geometry,
                    clearance=clearance,
                    purposes=frozenset(purposes),
                    affects=affects,
                    authority=str(item.get("authority", "project_policy")),
                    priority=str(item.get("priority", "hard")),
                    required=(
                        bool(item.get("required", False))
                        or (
                            bool(site.pedestrian_and_emergency.get("emergency_access_required", False))
                            and key in {"fire_lanes", "access_routes", "emergency_access_routes"}
                        )
                        or (site.parking_quotas.get("accessible_min", 0) > 0 and key == "accessible_routes")
                    ),
                )
            )
    return geometries


def _constraint_from_area(
    spec: SiteAreaSpec,
    source: str,
    purposes: frozenset[str],
) -> ConstraintGeometry:
    geometry = _declared_geometry(spec.geometry, f"{source} '{spec.id}'.geometry")
    return _make_constraint(
        constraint_id=spec.id,
        source=source,
        kind=spec.kind,
        geometry=geometry,
        clearance=spec.clearance,
        purposes=purposes,
        affects=spec.affects,
        authority=spec.authority,
        priority=spec.priority,
    )


def _make_constraint(
    *,
    constraint_id: str,
    source: str,
    kind: str,
    geometry: BaseGeometry,
    clearance: float,
    purposes: frozenset[str],
    affects: tuple[str, ...],
    authority: str,
    priority: str,
    required: bool = False,
) -> ConstraintGeometry:
    exclusion = geometry.buffer(clearance, join_style="mitre") if clearance else geometry
    return ConstraintGeometry(
        id=constraint_id,
        source=source,
        kind=kind,
        base_geometry=geometry,
        exclusion_geometry=exclusion,
        clearance=clearance,
        purposes=purposes,
        affects=affects,
        authority=authority,
        priority=priority,
        required=required,
    )


def _declared_geometry(raw: Any, label: str) -> BaseGeometry:
    if isinstance(raw, list):
        return _valid_polygon(raw, label)
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a geometry object")

    geometry_type = str(raw.get("type", "")).strip().lower()
    if geometry_type == "polygon":
        return _valid_polygon(raw.get("points"), label)
    if geometry_type == "circle":
        center = _point(raw.get("center"), f"{label}.center")
        radius = _positive_float(raw.get("radius"), f"{label}.radius")
        return ShapelyPoint(center).buffer(radius, quad_segs=16)
    if geometry_type == "rectangle":
        origin = _point(raw.get("origin"), f"{label}.origin")
        width = _positive_float(raw.get("width"), f"{label}.width")
        height = _positive_float(raw.get("height"), f"{label}.height")
        geometry = box(0.0, 0.0, width, height)
        geometry = affinity.rotate(
            geometry,
            float(raw.get("rotation_degrees", 0.0)),
            origin=(0.0, 0.0),
            use_radians=False,
        )
        return affinity.translate(geometry, xoff=origin[0], yoff=origin[1])
    if geometry_type == "polyline_buffer":
        points = [_point(value, f"{label}.points[]") for value in raw.get("points", [])]
        if len(points) < 2:
            raise ValueError(f"{label}.points must contain at least two points")
        width = _positive_float(raw.get("width"), f"{label}.width")
        return LineString(points).buffer(width / 2, cap_style="flat", join_style="mitre")
    raise ValueError(f"{label}.type={geometry_type!r} is not supported")


def _valid_polygon(raw: Any, label: str) -> ShapelyPolygon:
    if not isinstance(raw, list | tuple) or len(raw) < 3:
        raise ValueError(f"{label} must contain at least three points")
    points = [_point(value, f"{label}.points[]") for value in raw]
    geometry = ShapelyPolygon(points)
    if geometry.is_empty or geometry.area <= _AREA_TOLERANCE:
        raise ValueError(f"{label} must have positive area")
    if not geometry.is_valid:
        raise ValueError(f"{label} must be a valid polygon")
    return geometry


def _point(raw: Any, label: str) -> tuple[float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"{label} must be [x, y]")
    return (float(raw[0]), float(raw[1]))


def _positive_float(raw: Any, label: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _nonnegative_float(raw: Any, label: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError(f"{label} must be non-negative")
    return value


def _purposes_from_access_flags(parking_allowed: bool, vehicle_allowed: bool) -> set[str]:
    purposes: set[str] = set()
    if not parking_allowed:
        purposes.add("stall")
    if not vehicle_allowed:
        purposes.update({"aisle", "swept_path"})
    return purposes


def _purposes_from_affects(affects: tuple[str, ...]) -> set[str]:
    normalized = {value.strip().lower() for value in affects}
    purposes: set[str] = set()
    if normalized.intersection({"forbidden", "clearance_only", "vehicle_clearance"}):
        purposes.update(_CONCRETE_PURPOSES)
    if normalized.intersection(
        {"door_clearance", "no_parking", "parking", "stall", "stalls", "stall_access"}
    ):
        purposes.add("stall")
    if normalized.intersection({"aisle", "aisles", "aisle_clearance", "entrance_throat", "queueing"}):
        purposes.add("aisle")
    if normalized.intersection(
        {"maneuver", "stall_access", "turning", "swept_path", "swept_paths", "vehicle_swept_path"}
    ):
        purposes.add("swept_path")
    return purposes


def _normalize_purpose(purpose: ConstraintPurpose) -> ConstraintPurpose:
    if purpose not in {"all", *_CONCRETE_PURPOSES}:
        raise ValueError(f"unsupported site-constraint purpose: {purpose!r}")
    return purpose


def _matches_purpose(item: ConstraintGeometry, purpose: ConstraintPurpose) -> bool:
    return bool(item.purposes) if purpose == "all" else purpose in item.purposes


def _layout_object_conflicts(layout: LayoutResult, purpose: Literal["stall", "aisle"]) -> list[dict[str, Any]]:
    objects = layout.stalls if purpose == "stall" else layout.aisles
    boundary = _valid_polygon(layout.site.boundary, "site.boundary")
    setback_boundary = boundary.buffer(-layout.site.margin, join_style="mitre")
    conflicts: list[dict[str, Any]] = []
    for item in objects:
        geometry = _valid_polygon(item.polygon, f"{purpose} '{item.id}'")
        reasons: list[dict[str, Any]] = []
        if not setback_boundary.buffer(_AREA_TOLERANCE).covers(geometry):
            reasons.append(
                {
                    "constraint_id": "site-boundary-setback",
                    "source": "site_boundary",
                    "kind": "setback",
                    "purpose": purpose,
                    "overlap_area": float(geometry.difference(setback_boundary).area),
                    "clearance": layout.site.margin,
                }
            )
        reasons.extend(constraint_conflicts(layout.site, geometry, purpose))
        for reason in reasons:
            conflicts.append(
                {
                    "object_id": item.id,
                    "object_type": purpose,
                    **reason,
                    "message": (
                        f"{purpose} '{item.id}' conflicts with {reason['source']} "
                        f"'{reason['constraint_id']}'"
                    ),
                }
            )
    return conflicts


def _stall_specs_by_id(site: SiteSpec) -> dict[str, StallSpec]:
    specs = [site.stall, *site.stall_candidates]
    if site.main_stall is not None:
        specs.append(site.main_stall)
    if site.branch_stall is not None:
        specs.append(site.branch_stall)
    return {spec.id: spec for spec in specs}


def _has_hard_route_geometry(data: dict[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        raw_items = data.get(key, [])
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict) or "geometry" not in item:
                continue
            priority = str(item.get("priority", "hard")).strip().lower()
            authority = str(item.get("authority", "project_policy")).strip().lower()
            if (
                item.get("enabled", True)
                and authority != "advisory"
                and priority not in _ADVISORY_PRIORITIES
            ):
                return True
    return False


def _missing_required_route_geometry_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in (
        "pedestrian_routes",
        "accessible_routes",
        "fire_lanes",
        "access_routes",
        "emergency_access_routes",
    ):
        raw_items = data.get(key, [])
        if not isinstance(raw_items, list):
            continue
        for index, item in enumerate(raw_items, start=1):
            if isinstance(item, dict) and item.get("required", False) and "geometry" not in item:
                item_id = str(item.get("id", f"{key}-{index}"))
                errors.append(f"required {key.removesuffix('s')} '{item_id}' needs geometry")
    return errors


def _authority_summary(
    site: SiteSpec,
    declarations: list[ConstraintGeometry],
) -> dict[str, Any]:
    tiers: dict[str, Any] = {}
    for authority in _AUTHORITY_TIERS:
        items = [
            item
            for item in declarations
            if item.authority.strip().lower() == authority
        ]
        tiers[authority] = {
            "declared_count": len(items),
            "declared_ids": [item.id for item in items],
            "active_count": sum(1 for item in items if item.hard),
            "active_ids": [item.id for item in items if item.hard],
            "blocking": authority != "advisory",
        }
    metadata = _jurisdictional_metadata_status(site)
    metadata["required"] = bool(tiers["jurisdictional"]["declared_count"])
    if not metadata["required"]:
        metadata["valid"] = True
        metadata["missing"] = []
    tiers["jurisdictional"]["standards_metadata"] = metadata
    return tiers


def _jurisdictional_metadata_status(site: SiteSpec) -> dict[str, Any]:
    requirements = {
        "standards.standard_profile": site.standards.get("standard_profile"),
        "standards.source": site.standards.get("source"),
        "standards.effective_date": site.standards.get("effective_date"),
    }
    missing = [key for key, value in requirements.items() if not str(value or "").strip()]
    return {
        "valid": not missing,
        "missing": missing,
        "profile": requirements["standards.standard_profile"],
        "source": requirements["standards.source"],
        "effective_date": requirements["standards.effective_date"],
    }
