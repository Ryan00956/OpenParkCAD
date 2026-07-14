"""Conservative vehicle footprints and swept-path collision checks."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from shapely.affinity import rotate, translate
from shapely.geometry import GeometryCollection, LineString, Polygon as ShapelyPolygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openparkcad.models import ParkingAisle, ParkingStall, Point, VehicleSpec
from openparkcad.vehicle_kinematics import (
    KinematicResult,
    MotionSegment,
    VehiclePose,
    arc_motion,
    rear_axle_turning_radius,
    simulate_bicycle_path,
    straight_motion,
)

_GEOMETRY_EPSILON = 1e-9


@dataclass(frozen=True)
class SweptPathResult:
    """Kinematic trace plus its conservative body envelope and validation."""

    valid: bool
    reason: str | None
    kinematics: KinematicResult
    envelope: BaseGeometry = field(default_factory=GeometryCollection)
    collision_geometry: BaseGeometry = field(default_factory=GeometryCollection)
    colliding_obstacle_indices: tuple[int, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self, *, include_trajectory: bool = True, include_geometry: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible result suitable for validation reports."""

        record = self.kinematics.to_record(include_trajectory=include_trajectory)
        record.update(
            {
                "valid": self.valid,
                "reason": self.reason,
                "swept_area": self.envelope.area,
                "collision_area": self.collision_geometry.area,
                "colliding_obstacle_indices": list(self.colliding_obstacle_indices),
                "details": {**record.get("details", {}), **self.details},
            }
        )
        if include_geometry:
            record["envelope"] = mapping(self.envelope)
            record["collision_geometry"] = mapping(self.collision_geometry)
        return record


@dataclass(frozen=True)
class VehicleFootprintResolution:
    """Resolved body overhangs and any assumptions used."""

    valid: bool
    reason: str | None
    front_overhang: float | None
    rear_overhang: float | None
    assumptions: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "front_overhang": self.front_overhang,
            "rear_overhang": self.rear_overhang,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class ReverseIn90Template:
    """A deterministic one-arc-plus-straight reverse-in maneuver."""

    valid: bool
    reason: str | None
    start_pose: VehiclePose | None = None
    final_pose: VehiclePose | None = None
    segments: tuple[MotionSegment, ...] = ()
    variant: str | None = None
    front_edge: tuple[Point, Point] | None = None
    rear_axle_turn_radius: float | None = None
    envelope: BaseGeometry = field(default_factory=GeometryCollection)
    details: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "start_pose": _pose_record(self.start_pose),
            "final_pose": _pose_record(self.final_pose),
            "segments": [
                {
                    "distance": segment.distance,
                    "steering_angle_degrees": segment.steering_angle_degrees,
                    "label": segment.label,
                }
                for segment in self.segments
            ],
            "variant": self.variant,
            "front_edge": self.front_edge,
            "rear_axle_turn_radius": self.rear_axle_turn_radius,
            "swept_area": self.envelope.area,
            "details": dict(self.details),
        }


def vehicle_footprint(
    vehicle: VehicleSpec,
    pose: VehiclePose,
    *,
    include_margin: bool = True,
    rear_overhang: float | None = None,
) -> ShapelyPolygon:
    """Build the vehicle body polygon at one rear-axle-centre pose.

    The safety margin expands every side of the body.  When no rear overhang is
    supplied, the total non-wheelbase length is divided evenly between front
    and rear.
    """

    resolution = resolve_vehicle_overhangs(vehicle, rear_overhang=rear_overhang)
    if not resolution.valid or resolution.front_overhang is None or resolution.rear_overhang is None:
        raise ValueError(resolution.reason or "Vehicle overhangs cannot be resolved")
    if not all(math.isfinite(value) for value in (pose.x, pose.y, pose.heading_degrees)):
        raise ValueError("Vehicle pose must contain finite coordinates and heading")
    margin = vehicle.swept_path_margin if include_margin else 0.0
    if not math.isfinite(margin) or margin < 0.0:
        raise ValueError("Swept-path margin cannot be negative")

    assert vehicle.wheelbase is not None
    rear_x = -resolution.rear_overhang - margin
    front_x = vehicle.wheelbase + resolution.front_overhang + margin
    half_width = vehicle.width / 2.0 + margin
    local = ShapelyPolygon(
        [
            (rear_x, -half_width),
            (front_x, -half_width),
            (front_x, half_width),
            (rear_x, half_width),
        ]
    )
    rotated = rotate(local, pose.heading_degrees, origin=(0.0, 0.0), use_radians=False)
    return translate(rotated, xoff=pose.x, yoff=pose.y)


def resolve_vehicle_overhangs(
    vehicle: VehicleSpec,
    *,
    rear_overhang: float | None = None,
) -> VehicleFootprintResolution:
    """Resolve explicit or derivable front/rear body overhangs."""

    if not math.isfinite(vehicle.length) or vehicle.length <= 0.0:
        return VehicleFootprintResolution(False, "invalid_vehicle_length", None, None)
    if not math.isfinite(vehicle.width) or vehicle.width <= 0.0:
        return VehicleFootprintResolution(False, "invalid_vehicle_width", None, None)
    if vehicle.wheelbase is None or not math.isfinite(vehicle.wheelbase) or vehicle.wheelbase <= 0.0:
        return VehicleFootprintResolution(False, "vehicle_wheelbase_missing_or_invalid", None, None)
    remaining = vehicle.length - vehicle.wheelbase
    if remaining < -_GEOMETRY_EPSILON:
        return VehicleFootprintResolution(False, "vehicle_wheelbase_exceeds_length", None, None)

    explicit_front = vehicle.front_overhang
    explicit_rear = rear_overhang if rear_overhang is not None else vehicle.rear_overhang
    assumptions: list[str] = []
    if explicit_front is None and explicit_rear is None:
        explicit_front = remaining / 2.0
        explicit_rear = remaining / 2.0
        assumptions.append("front_and_rear_overhang_split_evenly")
    elif explicit_front is None:
        assert explicit_rear is not None
        explicit_front = remaining - explicit_rear
        assumptions.append("front_overhang_derived_from_vehicle_length")
    elif explicit_rear is None:
        explicit_rear = remaining - explicit_front
        assumptions.append("rear_overhang_derived_from_vehicle_length")

    if not math.isfinite(explicit_front) or not math.isfinite(explicit_rear):
        return VehicleFootprintResolution(False, "vehicle_overhang_not_finite", None, None)
    if explicit_front < 0.0 or explicit_rear < 0.0:
        return VehicleFootprintResolution(False, "vehicle_overhang_negative", None, None)
    if not math.isclose(explicit_front + explicit_rear, remaining, abs_tol=1e-6):
        return VehicleFootprintResolution(
            False,
            "vehicle_overhangs_inconsistent_with_length_and_wheelbase",
            explicit_front,
            explicit_rear,
        )
    return VehicleFootprintResolution(
        True,
        None,
        explicit_front,
        explicit_rear,
        tuple(assumptions),
    )


def conservative_swept_envelope(
    vehicle: VehicleSpec,
    poses: Sequence[VehiclePose],
    *,
    rear_overhang: float | None = None,
) -> BaseGeometry:
    """Union conservative between-sample body sweeps.

    The convex hull of each consecutive footprint pair intentionally
    over-approximates the short arc between them.  This prevents sampling gaps
    from becoming false-negative collision checks.
    """

    if not poses:
        return GeometryCollection()
    footprints = [vehicle_footprint(vehicle, pose, rear_overhang=rear_overhang) for pose in poses]
    if len(footprints) == 1:
        return footprints[0]
    slabs = [unary_union((first, second)).convex_hull for first, second in zip(footprints, footprints[1:])]
    return unary_union(slabs)


def validate_swept_path(
    vehicle: VehicleSpec,
    start_pose: VehiclePose,
    segments: list[MotionSegment] | tuple[MotionSegment, ...],
    *,
    boundary: BaseGeometry | Sequence[Point] | None = None,
    obstacles: Iterable[BaseGeometry | Sequence[Point]] = (),
    sample_step: float = 0.25,
    max_heading_step_degrees: float = 2.0,
    require_min_turning_radius: bool = True,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    rear_overhang: float | None = None,
) -> SweptPathResult:
    """Simulate, envelope, and validate a low-speed maneuver.

    Boundary contact is allowed, while obstacle contact is treated as a
    collision.  ``swept_path_margin`` is already embedded in every footprint.
    """

    kinematics = simulate_bicycle_path(
        vehicle,
        start_pose,
        segments,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_min_turning_radius=require_min_turning_radius,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not kinematics.valid:
        return SweptPathResult(False, kinematics.reason, kinematics, details=dict(kinematics.details))

    footprint_resolution = resolve_vehicle_overhangs(vehicle, rear_overhang=rear_overhang)
    footprint_details = {"footprint_resolution": footprint_resolution.to_record()}
    try:
        envelope = conservative_swept_envelope(vehicle, kinematics.poses, rear_overhang=rear_overhang)
    except ValueError as error:
        return SweptPathResult(
            False,
            "vehicle_footprint_invalid",
            kinematics,
            details={"message": str(error), **footprint_details},
        )

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return SweptPathResult(
            False,
            "invalid_geometry_tolerance",
            kinematics,
            envelope,
            details={"geometry_tolerance": geometry_tolerance, **footprint_details},
        )

    try:
        boundary_geometry = _as_polygon(boundary, "boundary") if boundary is not None else None
    except (TypeError, ValueError) as error:
        return SweptPathResult(
            False,
            "invalid_boundary_geometry",
            kinematics,
            envelope,
            details={"message": str(error), **footprint_details},
        )
    if boundary_geometry is not None:
        accepted_boundary = boundary_geometry.buffer(float(geometry_tolerance))
        if not accepted_boundary.covers(envelope):
            collision = envelope.difference(accepted_boundary)
            return SweptPathResult(
                False,
                "swept_path_outside_boundary",
                kinematics,
                envelope,
                collision,
                details={"outside_area": collision.area, **footprint_details},
            )

    try:
        obstacle_geometries = [_as_polygon(item, f"obstacles[{index}]") for index, item in enumerate(obstacles)]
    except (TypeError, ValueError) as error:
        return SweptPathResult(
            False,
            "invalid_obstacle_geometry",
            kinematics,
            envelope,
            details={"message": str(error), **footprint_details},
        )
    colliding_indices = tuple(
        index
        for index, obstacle in enumerate(obstacle_geometries)
        if envelope.intersects(obstacle.buffer(float(geometry_tolerance)))
    )
    if colliding_indices:
        collision = envelope.intersection(unary_union([obstacle_geometries[index] for index in colliding_indices]))
        return SweptPathResult(
            False,
            "swept_path_intersects_obstacle",
            kinematics,
            envelope,
            collision,
            colliding_indices,
            {"obstacle_count": len(colliding_indices), **footprint_details},
        )

    return SweptPathResult(True, None, kinematics, envelope, details=footprint_details)


def reverse_in_90_template(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> ReverseIn90Template:
    """Construct an audited reverse-in template for a perpendicular stall.

    Supported geometry is intentionally narrow and fail-closed: a straight
    stall front edge must touch the serving aisle, the vehicle body must fit in
    the final stall, and one of the two aisle-tangent approaches must keep the
    conservative envelope inside the supplied drivable area plus the target
    stall.  The maneuver is one 90-degree constant-radius reverse arc followed
    by a straight reverse segment.
    """

    try:
        stall_geometry = _parking_geometry(stall, "stall")
        aisle_geometry = _parking_geometry(serving_aisle, "serving_aisle")
        active_drivable = (
            _as_polygon(drivable_area, "drivable_area") if drivable_area is not None else aisle_geometry
        )
        explicit_reference_area = (
            _as_polygon(allowed_reference_area, "allowed_reference_area")
            if allowed_reference_area is not None
            else None
        )
    except (TypeError, ValueError) as error:
        return ReverseIn90Template(False, "invalid_stall_or_aisle_geometry", details={"message": str(error)})

    if (
        not isinstance(geometry_tolerance, int | float)
        or not math.isfinite(float(geometry_tolerance))
        or geometry_tolerance < 0.0
    ):
        return ReverseIn90Template(
            False,
            "invalid_geometry_tolerance",
            details={"geometry_tolerance": geometry_tolerance},
        )

    edge = _stall_front_edge(stall_geometry, aisle_geometry, geometry_tolerance)
    if edge is None:
        return ReverseIn90Template(False, "stall_front_edge_not_connected_to_serving_aisle")
    basis = _stall_basis(stall_geometry, edge, geometry_tolerance)
    if basis is None:
        return ReverseIn90Template(False, "unsupported_stall_geometry_for_reverse_in_90")
    midpoint, tangent, inward, stall_depth = basis
    crossing_rule = str(centerline_crossing).strip().lower()
    if crossing_rule not in {"allowed", "permitted", "maneuver_only", "forbidden"}:
        return ReverseIn90Template(
            False,
            "unsupported_centerline_crossing_rule",
            front_edge=_edge_points(edge),
            details={"centerline_crossing": centerline_crossing},
        )
    reference_area = None
    if crossing_rule == "forbidden":
        aisle_side_reference = (
            explicit_reference_area
            if explicit_reference_area is not None
            else _stall_side_reference_area(aisle_geometry, tangent, inward)
        )
        reference_area = unary_union((aisle_side_reference, stall_geometry))

    footprint_resolution = resolve_vehicle_overhangs(vehicle)
    if (
        not footprint_resolution.valid
        or footprint_resolution.front_overhang is None
        or footprint_resolution.rear_overhang is None
        or vehicle.wheelbase is None
    ):
        return ReverseIn90Template(
            False,
            footprint_resolution.reason or "vehicle_footprint_invalid",
            front_edge=_edge_points(edge),
            details={"footprint_resolution": footprint_resolution.to_record()},
        )
    if vehicle.width > edge.length + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_wide_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_width": vehicle.width, "stall_front_width": edge.length},
        )
    if vehicle.length > stall_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_too_long_for_stall",
            front_edge=_edge_points(edge),
            details={"vehicle_length": vehicle.length, "stall_depth": stall_depth},
        )

    radius_resolution = rear_axle_turning_radius(
        vehicle,
        require_explicit_track_width=require_explicit_track_width,
    )
    if not radius_resolution.valid or radius_resolution.rear_axle_radius is None:
        return ReverseIn90Template(
            False,
            radius_resolution.reason or "minimum_turning_radius_unresolved",
            front_edge=_edge_points(edge),
            details={"turning_radius_resolution": radius_resolution.to_record()},
        )

    front_extent = vehicle.wheelbase + footprint_resolution.front_overhang
    maximum_axle_depth = stall_depth - footprint_resolution.rear_overhang
    if front_extent > maximum_axle_depth + geometry_tolerance:
        return ReverseIn90Template(
            False,
            "vehicle_body_cannot_land_inside_stall",
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={
                "minimum_rear_axle_depth": front_extent,
                "maximum_rear_axle_depth": maximum_axle_depth,
                "footprint_resolution": footprint_resolution.to_record(),
            },
        )
    target_depth = (front_extent + maximum_axle_depth) / 2.0
    target_heading = _heading_degrees((-inward[0], -inward[1]))
    target_point = (
        midpoint[0] + inward[0] * target_depth,
        midpoint[1] + inward[1] * target_depth,
    )
    target_pose = VehiclePose(target_point[0], target_point[1], target_heading)
    target_body = vehicle_footprint(vehicle, target_pose, include_margin=False)
    if not stall_geometry.buffer(geometry_tolerance).covers(target_body):
        return ReverseIn90Template(
            False,
            "reverse_in_90_final_pose_outside_stall",
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"target_depth": target_depth},
        )

    margin = max(vehicle.swept_path_margin, 0.0)
    allowed_area = unary_union((active_drivable, stall_geometry.buffer(margin)))
    candidates: list[tuple[float, float, float, int, ReverseIn90Template]] = []
    failures: list[tuple[str, dict[str, Any]]] = []
    tangent_variants = ((tangent, "front_edge_start_to_end"), ((-tangent[0], -tangent[1]), "front_edge_end_to_start"))
    for approach_tangent, variant in tangent_variants:
        approach_heading = _heading_degrees(approach_tangent)
        heading_change = _signed_heading_delta(approach_heading, target_heading)
        if not math.isclose(abs(heading_change), 90.0, abs_tol=1e-5):
            failures.append(("stall_not_perpendicular_to_serving_aisle", {"heading_change": heading_change}))
            continue
        turn = arc_motion(
            vehicle,
            heading_change,
            radius=radius_resolution.rear_axle_radius,
            reverse=True,
            label="reverse_90_arc",
        )
        arc_from_origin = simulate_bicycle_path(
            vehicle,
            VehiclePose(0.0, 0.0, approach_heading),
            [turn],
            sample_step=sample_step,
            max_heading_step_degrees=max_heading_step_degrees,
            require_explicit_track_width=require_explicit_track_width,
        )
        if not arc_from_origin.valid or arc_from_origin.final_pose is None:
            failures.append((arc_from_origin.reason or "reverse_arc_simulation_failed", arc_from_origin.details))
            continue
        arc_end = arc_from_origin.final_pose
        for arc_end_depth in _template_arc_end_depths(target_depth):
            arc_target = (
                midpoint[0] + inward[0] * arc_end_depth,
                midpoint[1] + inward[1] * arc_end_depth,
            )
            start_pose = VehiclePose(
                arc_target[0] - arc_end.x,
                arc_target[1] - arc_end.y,
                approach_heading,
            )
            commands = (
                turn,
                straight_motion(target_depth - arc_end_depth, reverse=True, label="reverse_into_stall"),
            )
            simulation = simulate_bicycle_path(
                vehicle,
                start_pose,
                commands,
                sample_step=sample_step,
                max_heading_step_degrees=max_heading_step_degrees,
                require_explicit_track_width=require_explicit_track_width,
            )
            if not simulation.valid or simulation.final_pose is None:
                failures.append((simulation.reason or "reverse_in_90_simulation_failed", simulation.details))
                continue
            envelope = conservative_swept_envelope(vehicle, simulation.poses)
            outside = envelope.difference(allowed_area.buffer(geometry_tolerance))
            trajectory = LineString([(pose.x, pose.y) for pose in simulation.poses])
            reference_outside_length = 0.0
            if reference_area is not None:
                reference_outside_length = trajectory.difference(reference_area.buffer(geometry_tolerance)).length
            within_reference_area = reference_outside_length <= _GEOMETRY_EPSILON
            within_drivable_area = outside.area <= _GEOMETRY_EPSILON
            if not within_reference_area:
                failure_reason = "rear_axle_crosses_forbidden_aisle_centerline"
            elif not within_drivable_area:
                failure_reason = "serving_aisle_too_narrow_for_reverse_in_90"
            else:
                failure_reason = None
            candidate_details = {
                "template_version": "reverse_in_90_bicycle_v1",
                "turning_radius_resolution": radius_resolution.to_record(),
                "footprint_resolution": footprint_resolution.to_record(),
                "target_rear_axle_depth": target_depth,
                "arc_end_depth": arc_end_depth,
                "arc_end_depth_search_step": min(0.25, target_depth) if target_depth > 0.0 else 0.0,
                "stall_depth": stall_depth,
                "stall_front_width": edge.length,
                "reverse_distance": simulation.reverse_distance,
                "path_length": simulation.path_length,
                "outside_drivable_area": outside.area,
                "centerline_crossing": crossing_rule,
                "reference_path_outside_length": reference_outside_length,
                "sampling": {
                    "maximum_distance_step": sample_step,
                    "maximum_heading_step_degrees": max_heading_step_degrees,
                },
                "geometry_assumptions": [
                    "front_edge_connected_to_serving_aisle",
                    "single_constant_radius_reverse_arc_then_reverse_straight",
                    "arc_may_finish_after_crossing_the_stall_front_edge",
                    "final_unmargined_vehicle_body_must_fit_target_stall",
                    "swept_margin_allowed_within_buffered_target_stall",
                    (
                        "explicit_allowed_reference_area"
                        if explicit_reference_area is not None
                        else "forbidden_centerline_uses_aisle_centroid_half_plane"
                    )
                    if crossing_rule == "forbidden"
                    else "centerline_crossing_allowed_for_maneuver",
                ],
            }
            candidate = ReverseIn90Template(
                within_reference_area and within_drivable_area,
                failure_reason,
                start_pose,
                simulation.final_pose,
                commands,
                variant,
                _edge_points(edge),
                radius_resolution.rear_axle_radius,
                envelope,
                candidate_details,
            )
            candidates.append(
                (
                    reference_outside_length,
                    outside.area,
                    arc_end_depth,
                    0 if variant == "front_edge_start_to_end" else 1,
                    candidate,
                )
            )
            if candidate.valid:
                break

    if not candidates:
        reason, details = failures[0] if failures else ("reverse_in_90_template_not_constructible", {})
        return ReverseIn90Template(
            False,
            reason,
            final_pose=target_pose,
            front_edge=_edge_points(edge),
            rear_axle_turn_radius=radius_resolution.rear_axle_radius,
            details={"failures": [{"reason": item[0], "details": item[1]} for item in failures], **details},
        )
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][4]


def validate_stall_swept_path(
    vehicle: VehicleSpec,
    stall: ParkingStall | BaseGeometry | Sequence[Point],
    serving_aisle: ParkingAisle | BaseGeometry | Sequence[Point],
    *,
    boundary: BaseGeometry | Sequence[Point] | None = None,
    obstacles: Iterable[BaseGeometry | Sequence[Point]] = (),
    drivable_area: BaseGeometry | Sequence[Point] | None = None,
    centerline_crossing: str = "allowed",
    allowed_reference_area: BaseGeometry | Sequence[Point] | None = None,
    require_explicit_track_width: bool = True,
    geometry_tolerance: float = 1e-7,
    sample_step: float = 0.2,
    max_heading_step_degrees: float = 1.5,
) -> SweptPathResult:
    """Build and spatially validate the supported perpendicular-90 template."""

    template = reverse_in_90_template(
        vehicle,
        stall,
        serving_aisle,
        drivable_area=drivable_area,
        centerline_crossing=centerline_crossing,
        allowed_reference_area=allowed_reference_area,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
    )
    template_record = template.to_record()
    if template.start_pose is None or not template.segments:
        kinematics = KinematicResult(
            False,
            template.reason,
            (),
            details={"template": template_record},
        )
        return SweptPathResult(
            False,
            template.reason,
            kinematics,
            template.envelope,
            details={"template": template_record},
        )
    if not template.valid:
        kinematics = simulate_bicycle_path(
            vehicle,
            template.start_pose,
            template.segments,
            sample_step=sample_step,
            max_heading_step_degrees=max_heading_step_degrees,
            require_explicit_track_width=require_explicit_track_width,
        )
        return SweptPathResult(
            False,
            template.reason,
            kinematics,
            template.envelope,
            details={"template": template_record},
        )

    result = validate_swept_path(
        vehicle,
        template.start_pose,
        template.segments,
        boundary=boundary,
        obstacles=obstacles,
        sample_step=sample_step,
        max_heading_step_degrees=max_heading_step_degrees,
        require_explicit_track_width=require_explicit_track_width,
        geometry_tolerance=geometry_tolerance,
    )
    return SweptPathResult(
        result.valid,
        result.reason,
        result.kinematics,
        result.envelope,
        result.collision_geometry,
        result.colliding_obstacle_indices,
        {**result.details, "template": template_record},
    )


def _as_polygon(raw: BaseGeometry | Sequence[Point], label: str) -> BaseGeometry:
    geometry = raw if isinstance(raw, BaseGeometry) else ShapelyPolygon(raw)
    if geometry.is_empty or not geometry.is_valid or geometry.area <= _GEOMETRY_EPSILON:
        raise ValueError(f"{label} must be a non-empty valid area geometry")
    return geometry


def _parking_geometry(
    raw: ParkingStall | ParkingAisle | BaseGeometry | Sequence[Point],
    label: str,
) -> BaseGeometry:
    if isinstance(raw, ParkingStall | ParkingAisle):
        return _as_polygon(raw.polygon, label)
    return _as_polygon(raw, label)


def _stall_front_edge(stall: BaseGeometry, aisle: BaseGeometry, tolerance: float) -> LineString | None:
    if not isinstance(stall, ShapelyPolygon):
        return None
    coords = list(stall.exterior.coords)
    candidates: list[tuple[float, float, float, LineString]] = []
    contact_area = aisle.buffer(max(tolerance, 1e-6))
    for start, end in zip(coords, coords[1:]):
        edge = LineString((start, end))
        if edge.length <= _GEOMETRY_EPSILON:
            continue
        contact_length = edge.intersection(contact_area).length
        contact_ratio = contact_length / edge.length
        if contact_ratio + 1e-6 < 0.5:
            continue
        candidates.append((-contact_ratio, edge.distance(aisle), edge.length, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return candidates[0][3]


def _stall_basis(
    stall: BaseGeometry,
    edge: LineString,
    tolerance: float,
) -> tuple[Point, Point, Point, float] | None:
    edge_coords = list(edge.coords)
    if len(edge_coords) < 2:
        return None
    start, end = edge_coords[0], edge_coords[-1]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= _GEOMETRY_EPSILON:
        return None
    tangent = (dx / length, dy / length)
    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    centroid_vector = (stall.centroid.x - midpoint[0], stall.centroid.y - midpoint[1])
    along_edge = centroid_vector[0] * tangent[0] + centroid_vector[1] * tangent[1]
    normal_vector = (
        centroid_vector[0] - along_edge * tangent[0],
        centroid_vector[1] - along_edge * tangent[1],
    )
    normal_length = math.hypot(*normal_vector)
    if normal_length <= max(tolerance, _GEOMETRY_EPSILON):
        return None
    inward = (normal_vector[0] / normal_length, normal_vector[1] / normal_length)
    projections = [
        (point[0] - midpoint[0]) * inward[0] + (point[1] - midpoint[1]) * inward[1]
        for point in list(stall.exterior.coords)[:-1]
    ]
    if min(projections) < -max(tolerance, 1e-6):
        return None
    depth = max(projections)
    if depth <= _GEOMETRY_EPSILON:
        return None
    return midpoint, tangent, inward, depth


def _heading_degrees(vector: Point) -> float:
    return math.degrees(math.atan2(vector[1], vector[0])) % 360.0


def _signed_heading_delta(start: float, end: float) -> float:
    return (end - start + 180.0) % 360.0 - 180.0


def _edge_points(edge: LineString) -> tuple[Point, Point]:
    coords = list(edge.coords)
    return (float(coords[0][0]), float(coords[0][1])), (float(coords[-1][0]), float(coords[-1][1]))


def _template_arc_end_depths(target_depth: float) -> tuple[float, ...]:
    if target_depth <= _GEOMETRY_EPSILON:
        return (0.0,)
    step = min(0.25, target_depth)
    count = math.floor(target_depth / step)
    values = [index * step for index in range(count + 1)]
    if not math.isclose(values[-1], target_depth, abs_tol=_GEOMETRY_EPSILON):
        values.append(target_depth)
    return tuple(values)


def _stall_side_reference_area(aisle: BaseGeometry, tangent: Point, inward: Point) -> BaseGeometry:
    min_x, min_y, max_x, max_y = aisle.bounds
    extent = max(max_x - min_x, max_y - min_y, 1.0) * 10.0
    center = (aisle.centroid.x, aisle.centroid.y)
    half_plane = ShapelyPolygon(
        [
            (center[0] - tangent[0] * extent, center[1] - tangent[1] * extent),
            (center[0] + tangent[0] * extent, center[1] + tangent[1] * extent),
            (
                center[0] + tangent[0] * extent + inward[0] * extent,
                center[1] + tangent[1] * extent + inward[1] * extent,
            ),
            (
                center[0] - tangent[0] * extent + inward[0] * extent,
                center[1] - tangent[1] * extent + inward[1] * extent,
            ),
        ]
    )
    return aisle.intersection(half_plane)


def _pose_record(pose: VehiclePose | None) -> dict[str, float] | None:
    if pose is None:
        return None
    return {"x": pose.x, "y": pose.y, "heading_degrees": pose.heading_degrees}
