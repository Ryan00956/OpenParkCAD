from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]
Polygon = list[Point]


@dataclass(frozen=True)
class StallSpec:
    width: float = 2.5
    length: float = 5.3
    id: str = "standard"
    family: str = "perpendicular"
    allowed_angles: tuple[float, ...] = (0.0,)
    drive_over: bool = False
    access_sides: tuple[str, ...] = ("front",)
    blocked_sides: tuple[str, ...] = ()


@dataclass(frozen=True)
class EntranceSpec:
    id: str
    mode: str
    center: Point
    width: float
    heading_degrees: float
    allowed_movements: tuple[str, ...] = ("enter", "exit")


@dataclass(frozen=True)
class VehicleSpec:
    id: str = "passenger-car"
    length: float = 4.8
    width: float = 1.9
    wheelbase: float | None = None
    min_turning_radius: float | None = None
    swept_path_margin: float = 0.0
    max_reverse_distance: float | None = None


@dataclass(frozen=True)
class AisleClassSpec:
    id: str
    width: float
    capacity: str = "two_vehicle"
    directionality: str = "two_way"
    centerline_crossing: str = "forbidden"
    enabled: bool = True


@dataclass(frozen=True)
class SiteSpec:
    name: str
    boundary: Polygon
    obstacles: list[Polygon] = field(default_factory=list)
    stall: StallSpec = field(default_factory=StallSpec)
    aisle_width: float = 6.0
    angle_degrees: float = 0.0
    candidate_angles: tuple[float, ...] = (0.0,)
    margin: float = 0.2
    version: str = "legacy"
    units: str = "m"
    standards: dict[str, Any] = field(default_factory=dict)
    entrances: list[EntranceSpec] = field(default_factory=list)
    vehicle: VehicleSpec | None = None
    aisle_classes: list[AisleClassSpec] = field(default_factory=list)
    aisle_selection_mode: str = "fixed"
    fixed_aisle_class: str | None = None
    site_features: list[dict[str, Any]] = field(default_factory=list)
    pedestrian_and_emergency: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    optimization: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_format: str = "legacy"


@dataclass(frozen=True)
class ParkingStall:
    id: str
    polygon: Polygon
    angle_degrees: float


@dataclass(frozen=True)
class ParkingAisle:
    id: str
    polygon: Polygon
    angle_degrees: float


@dataclass(frozen=True)
class AngleAttempt:
    angle_degrees: float
    stall_count: int


@dataclass(frozen=True)
class LayoutResult:
    site: SiteSpec
    stalls: list[ParkingStall]
    aisles: list[ParkingAisle] = field(default_factory=list)
    selected_angle_degrees: float = 0.0
    attempts: list[AngleAttempt] = field(default_factory=list)

    @property
    def stall_count(self) -> int:
        return len(self.stalls)


def _point(raw: Any) -> Point:
    if not isinstance(raw, list | tuple) or len(raw) != 2:
        raise ValueError(f"Point must be [x, y], got {raw!r}")
    return (float(raw[0]), float(raw[1]))


def _polygon(raw: Any) -> Polygon:
    if not isinstance(raw, list) or len(raw) < 3:
        raise ValueError("Polygon must contain at least three points")
    return [_point(item) for item in raw]


def site_from_dict(data: dict[str, Any]) -> SiteSpec:
    if "site" in data:
        return _phase0_site_from_dict(data)
    return _legacy_site_from_dict(data)


def _legacy_site_from_dict(data: dict[str, Any]) -> SiteSpec:
    stall_data = data.get("stall", {})
    angle_degrees_raw = data.get("angle_degrees", 0.0)
    candidate_angles_raw = data.get("candidate_angles", angle_degrees_raw)
    if isinstance(candidate_angles_raw, list):
        candidate_angles = tuple(float(item) for item in candidate_angles_raw)
        angle_degrees = candidate_angles[0] if candidate_angles else 0.0
    else:
        angle_degrees = float(candidate_angles_raw)
        candidate_angles = (angle_degrees,)
    stall = StallSpec(
        id=str(stall_data.get("id", "standard")),
        width=float(stall_data.get("width", 2.5)),
        length=float(stall_data.get("length", 5.3)),
        allowed_angles=candidate_angles,
    )

    return SiteSpec(
        name=str(data.get("name", "parking site")),
        boundary=_polygon(data["boundary"]),
        obstacles=[_polygon(item) for item in data.get("obstacles", [])],
        stall=stall,
        aisle_width=float(data.get("aisle_width", 6.0)),
        angle_degrees=angle_degrees,
        candidate_angles=candidate_angles,
        margin=float(data.get("margin", 0.2)),
        source_format="legacy",
    )


def _phase0_site_from_dict(data: dict[str, Any]) -> SiteSpec:
    site_data = _dict(data.get("site"), "site")
    parking_data = _dict(data.get("parking", {}), "parking")
    aisles_data = _dict(data.get("aisles", {}), "aisles")
    constraints = _dict(data.get("constraints", {}), "constraints")

    boundary = _geometry_polygon(site_data["boundary"], "site.boundary")
    obstacles = [
        _obstacle_polygon(item, index)
        for index, item in enumerate(site_data.get("obstacles", []), start=1)
    ]

    stall = _active_stall_spec(parking_data)
    aisle_classes = [_aisle_class(item) for item in aisles_data.get("classes", [])]
    fixed_aisle_class = aisles_data.get("fixed_class")
    aisle_width = _selected_aisle_width(aisle_classes, fixed_aisle_class, default=6.0)
    margin = float(_dict(constraints.get("setbacks", {}), "constraints.setbacks").get("site_boundary", 0.2))

    entrances = [_entrance(item, index) for index, item in enumerate(data.get("entrances", []), start=1)]
    vehicle_data = _dict(data.get("vehicles", {}), "vehicles").get("design_vehicle")
    vehicle = _vehicle(vehicle_data) if vehicle_data else None

    return SiteSpec(
        version=str(data.get("version", "0.1")),
        name=str(data.get("name", "parking site")),
        units=str(data.get("units", "m")),
        standards=_dict(data.get("standards", {}), "standards"),
        boundary=boundary,
        obstacles=obstacles,
        stall=stall,
        aisle_width=aisle_width,
        angle_degrees=stall.allowed_angles[0],
        candidate_angles=_stall_candidate_angles(parking_data),
        margin=margin,
        entrances=entrances,
        vehicle=vehicle,
        aisle_classes=aisle_classes,
        aisle_selection_mode=str(aisles_data.get("selection_mode", "fixed")),
        fixed_aisle_class=str(fixed_aisle_class) if fixed_aisle_class else None,
        site_features=list(data.get("site_features", [])),
        pedestrian_and_emergency=_dict(data.get("pedestrian_and_emergency", {}), "pedestrian_and_emergency"),
        constraints=constraints,
        optimization=_dict(data.get("optimization", {}), "optimization"),
        diagnostics=_dict(data.get("diagnostics", {}), "diagnostics"),
        metadata=_dict(data.get("metadata", {}), "metadata"),
        source_format="phase0",
    )


def _dict(raw: Any, label: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    return raw


def _geometry_polygon(raw: Any, label: str) -> Polygon:
    if isinstance(raw, list):
        return _polygon(raw)
    geometry = _dict(raw, label)
    geometry_type = geometry.get("type")
    if geometry_type == "polygon":
        return _polygon(geometry["points"])
    raise ValueError(f"{label}.type={geometry_type!r} is documented but not implemented yet")


def _obstacle_polygon(raw: Any, index: int) -> Polygon:
    if isinstance(raw, list):
        return _polygon(raw)
    item = _dict(raw, f"site.obstacles[{index}]")
    return _geometry_polygon(item["geometry"], f"site.obstacles[{index}].geometry")


def _active_stall_spec(parking_data: dict[str, Any]) -> StallSpec:
    stall_types = parking_data.get("stall_types", [])
    enabled = [item for item in stall_types if item.get("enabled", True)]
    if enabled:
        data = enabled[0]
    else:
        data = stall_types[0] if stall_types else {}
    return StallSpec(
        id=str(data.get("id", "standard")),
        family=str(data.get("family", "perpendicular")),
        width=float(data.get("width", 2.5)),
        length=float(data.get("length", 5.3)),
        allowed_angles=tuple(float(item) for item in data.get("allowed_angles", [0])),
        drive_over=bool(data.get("drive_over", False)),
        access_sides=tuple(str(item) for item in data.get("access_sides", ["front"])),
        blocked_sides=tuple(str(item) for item in data.get("blocked_sides", [])),
    )


def _stall_candidate_angles(parking_data: dict[str, Any]) -> tuple[float, ...]:
    angles: list[float] = []
    for stall_type in parking_data.get("stall_types", []):
        if not stall_type.get("enabled", True):
            continue
        for angle in stall_type.get("allowed_angles", [0]):
            value = float(angle)
            if value not in angles:
                angles.append(value)
    return tuple(angles or [0.0])


def _aisle_class(raw: Any) -> AisleClassSpec:
    data = _dict(raw, "aisles.classes[]")
    return AisleClassSpec(
        id=str(data["id"]),
        width=float(data.get("width", 6.0)),
        capacity=str(data.get("capacity", "two_vehicle")),
        directionality=str(data.get("directionality", "two_way")),
        centerline_crossing=str(data.get("centerline_crossing", "forbidden")),
        enabled=bool(data.get("enabled", True)),
    )


def _selected_aisle_width(classes: list[AisleClassSpec], fixed_class: Any, default: float) -> float:
    if fixed_class:
        for aisle_class in classes:
            if aisle_class.id == fixed_class:
                return aisle_class.width
    for aisle_class in classes:
        if aisle_class.enabled:
            return aisle_class.width
    return default


def _entrance(raw: Any, index: int) -> EntranceSpec:
    data = _dict(raw, f"entrances[{index}]")
    return EntranceSpec(
        id=str(data.get("id", f"entrance-{index}")),
        mode=str(data.get("mode", "shared")),
        center=_point(data["center"]),
        width=float(data.get("width", 6.0)),
        heading_degrees=float(data.get("heading_degrees", 0.0)),
        allowed_movements=tuple(str(item) for item in data.get("allowed_movements", ["enter", "exit"])),
    )


def _vehicle(raw: Any) -> VehicleSpec:
    data = _dict(raw, "vehicles.design_vehicle")
    return VehicleSpec(
        id=str(data.get("id", "passenger-car")),
        length=float(data.get("length", 4.8)),
        width=float(data.get("width", 1.9)),
        wheelbase=float(data["wheelbase"]) if "wheelbase" in data else None,
        min_turning_radius=float(data["min_turning_radius"]) if "min_turning_radius" in data else None,
        swept_path_margin=float(data.get("swept_path_margin", 0.0)),
        max_reverse_distance=float(data["max_reverse_distance"]) if "max_reverse_distance" in data else None,
    )
