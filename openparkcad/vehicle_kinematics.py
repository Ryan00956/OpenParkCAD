"""Deterministic low-speed bicycle kinematics for vehicle maneuvers.

The pose reference is the centre of the rear axle.  ``min_turning_radius`` is
resolved according to ``VehicleSpec.turning_radius_reference`` before any
command is checked.  This avoids silently treating the common curb-to-curb
outer-front-wheel radius as a rear-axle centreline radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from openparkcad.models import VehicleSpec

_EPSILON = 1e-9


@dataclass(frozen=True)
class VehiclePose:
    """Rear-axle-centre pose in world coordinates."""

    x: float
    y: float
    heading_degrees: float = 0.0


@dataclass(frozen=True)
class MotionSegment:
    """Constant-steering motion segment.

    Distance is signed: positive values move forward and negative values move
    in reverse.  Positive steering turns the front wheels left.
    """

    distance: float
    steering_angle_degrees: float = 0.0
    label: str = ""

    @property
    def direction(self) -> str:
        return "reverse" if self.distance < 0.0 else "forward"


@dataclass(frozen=True)
class SegmentAudit:
    """Resolved geometry for one motion command."""

    index: int
    label: str
    direction: str
    distance: float
    steering_angle_degrees: float
    turn_radius: float | None
    heading_change_degrees: float


@dataclass(frozen=True)
class TurningRadiusResolution:
    """Auditable conversion of the declared radius to a rear-axle radius."""

    valid: bool
    reason: str | None
    input_radius: float | None
    input_reference: str
    rear_axle_radius: float | None
    formula: str | None = None
    track_width: float | None = None
    assumptions: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reason": self.reason,
            "input_radius": self.input_radius,
            "input_reference": self.input_reference,
            "rear_axle_radius": self.rear_axle_radius,
            "formula": self.formula,
            "track_width": self.track_width,
            "assumptions": list(self.assumptions),
        }


@dataclass(frozen=True)
class KinematicResult:
    """A completed or rejected kinematic simulation."""

    valid: bool
    reason: str | None
    poses: tuple[VehiclePose, ...]
    segments: tuple[SegmentAudit, ...] = ()
    reverse_distance: float = 0.0
    minimum_turn_radius_used: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def final_pose(self) -> VehiclePose | None:
        return self.poses[-1] if self.poses else None

    @property
    def path_length(self) -> float:
        return sum(abs(item.distance) for item in self.segments)

    def to_record(self, *, include_trajectory: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible audit record."""

        record: dict[str, Any] = {
            "valid": self.valid,
            "reason": self.reason,
            "reverse_distance": self.reverse_distance,
            "minimum_turn_radius_used": self.minimum_turn_radius_used,
            "path_length": self.path_length,
            "pose_count": len(self.poses),
            "segments": [
                {
                    "index": item.index,
                    "label": item.label,
                    "direction": item.direction,
                    "distance": item.distance,
                    "steering_angle_degrees": item.steering_angle_degrees,
                    "turn_radius": item.turn_radius,
                    "heading_change_degrees": item.heading_change_degrees,
                }
                for item in self.segments
            ],
            "details": dict(self.details),
        }
        if include_trajectory:
            record["trajectory"] = [
                {"x": pose.x, "y": pose.y, "heading_degrees": pose.heading_degrees}
                for pose in self.poses
            ]
        return record


def straight_motion(distance: float, *, reverse: bool = False, label: str = "straight") -> MotionSegment:
    """Build a zero-steering motion segment."""

    magnitude = abs(float(distance))
    return MotionSegment(distance=-magnitude if reverse else magnitude, label=label)


def arc_motion(
    vehicle: VehicleSpec,
    heading_change_degrees: float,
    *,
    radius: float | None = None,
    reverse: bool = False,
    label: str = "arc",
    require_explicit_track_width: bool = True,
) -> MotionSegment:
    """Build a constant-radius arc with a requested world heading change.

    The steering sign is adjusted for reverse motion, so a positive requested
    heading change always means the final heading rotates counter-clockwise.
    Radius validation is deliberately left to :func:`simulate_bicycle_path` so
    rejected templates still produce a structured failure reason.
    """

    if vehicle.wheelbase is None or vehicle.wheelbase <= 0.0:
        raise ValueError("A positive vehicle wheelbase is required to build an arc")
    if radius is None:
        resolution = rear_axle_turning_radius(
            vehicle,
            require_explicit_track_width=require_explicit_track_width,
        )
        if not resolution.valid or resolution.rear_axle_radius is None:
            raise ValueError(resolution.reason or "Vehicle turning radius cannot be resolved")
        active_radius = resolution.rear_axle_radius
    else:
        active_radius = radius
    if active_radius is None or not math.isfinite(active_radius) or active_radius <= 0.0:
        raise ValueError("A positive turn radius is required to build an arc")

    delta_radians = math.radians(float(heading_change_degrees))
    distance = abs(delta_radians) * active_radius
    if reverse:
        distance = -distance
    if abs(delta_radians) <= _EPSILON:
        return MotionSegment(distance=distance, label=label)

    curvature = delta_radians / distance
    steering = math.degrees(math.atan(vehicle.wheelbase * curvature))
    return MotionSegment(distance=distance, steering_angle_degrees=steering, label=label)


def turn_radius_for_steering(wheelbase: float, steering_angle_degrees: float) -> float | None:
    """Return the signed rear-axle path radius, or ``None`` for straight travel."""

    angle = math.radians(float(steering_angle_degrees))
    tangent = math.tan(angle)
    if abs(tangent) <= _EPSILON:
        return None
    return float(wheelbase) / tangent


def steering_angle_for_radius(wheelbase: float, radius: float) -> float:
    """Return the front-wheel steering angle for a signed path radius."""

    if not math.isfinite(radius) or abs(radius) <= _EPSILON:
        raise ValueError("Turn radius must be finite and non-zero")
    if not math.isfinite(wheelbase) or wheelbase <= 0.0:
        raise ValueError("Wheelbase must be finite and positive")
    return math.degrees(math.atan(float(wheelbase) / float(radius)))


def rear_axle_turning_radius(
    vehicle: VehicleSpec,
    *,
    require_explicit_track_width: bool = True,
) -> TurningRadiusResolution:
    """Resolve the declared minimum radius to the rear-axle reference path.

    ``outer_front_wheel`` uses the low-speed bicycle relationship

    ``R_rear = sqrt(R_outer**2 - wheelbase**2) - track_width/2``.

    In non-hard exploratory calls, a missing track width may be replaced by
    zero.  That yields a larger required rear-axle radius and is therefore a
    conservative approximation.  Hard calls fail closed instead.
    """

    reference = str(vehicle.turning_radius_reference).strip().lower()
    declared = vehicle.min_turning_radius
    if declared is None:
        return TurningRadiusResolution(False, "minimum_turning_radius_missing", None, reference, None)
    if not math.isfinite(declared) or declared <= 0.0:
        return TurningRadiusResolution(
            False,
            "invalid_minimum_turning_radius",
            declared,
            reference,
            None,
        )
    if vehicle.wheelbase is None or not math.isfinite(vehicle.wheelbase) or vehicle.wheelbase <= 0.0:
        return TurningRadiusResolution(
            False,
            "vehicle_wheelbase_missing_or_invalid",
            declared,
            reference,
            None,
        )
    if not math.isfinite(vehicle.length) or vehicle.length <= 0.0 or vehicle.wheelbase > vehicle.length + _EPSILON:
        return TurningRadiusResolution(
            False,
            "vehicle_wheelbase_exceeds_length",
            declared,
            reference,
            None,
        )
    if reference == "rear_axle_center":
        return TurningRadiusResolution(
            True,
            None,
            declared,
            reference,
            declared,
            formula="R_rear = R_input",
        )
    if reference != "outer_front_wheel":
        return TurningRadiusResolution(
            False,
            "unsupported_turning_radius_reference",
            declared,
            reference,
            None,
        )
    if not math.isfinite(vehicle.width) or vehicle.width <= 0.0:
        return TurningRadiusResolution(
            False,
            "invalid_vehicle_dimensions",
            declared,
            reference,
            None,
        )

    assumptions: tuple[str, ...] = ()
    declared_track_width = vehicle.track_width
    track_width = declared_track_width
    if declared_track_width is None:
        if require_explicit_track_width:
            return TurningRadiusResolution(
                False,
                "vehicle_track_width_missing",
                declared,
                reference,
                None,
            )
        track_width = 0.0
        assumptions = ("track_width_assumed_zero_for_conservative_radius",)
    if not math.isfinite(track_width) or (declared_track_width is not None and track_width <= 0.0):
        return TurningRadiusResolution(
            False,
            "vehicle_track_width_invalid",
            declared,
            reference,
            None,
            track_width=track_width,
        )
    if track_width < 0.0 or track_width > vehicle.width + _EPSILON:
        return TurningRadiusResolution(
            False,
            "vehicle_track_width_invalid",
            declared,
            reference,
            None,
            track_width=track_width,
        )

    radial_square = declared * declared - vehicle.wheelbase * vehicle.wheelbase
    if radial_square <= _EPSILON:
        return TurningRadiusResolution(
            False,
            "outer_turning_radius_incompatible_with_wheelbase",
            declared,
            reference,
            None,
            track_width=track_width,
        )
    rear_radius = math.sqrt(radial_square) - track_width / 2.0
    if rear_radius <= _EPSILON:
        return TurningRadiusResolution(
            False,
            "outer_turning_radius_incompatible_with_track_width",
            declared,
            reference,
            None,
            track_width=track_width,
        )
    return TurningRadiusResolution(
        True,
        None,
        declared,
        reference,
        rear_radius,
        formula="R_rear = sqrt(R_outer^2 - wheelbase^2) - track_width/2",
        track_width=track_width,
        assumptions=assumptions,
    )


def simulate_bicycle_path(
    vehicle: VehicleSpec,
    start_pose: VehiclePose,
    segments: list[MotionSegment] | tuple[MotionSegment, ...],
    *,
    sample_step: float = 0.25,
    max_heading_step_degrees: float = 2.0,
    require_min_turning_radius: bool = True,
    require_explicit_track_width: bool = True,
) -> KinematicResult:
    """Simulate constant-steering commands with exact bicycle-model updates.

    Sampling controls only the returned trajectory and later swept-envelope
    accuracy.  Each substep uses the closed-form constant-curvature solution,
    so the final pose does not accumulate Euler integration error.
    """

    input_failure = _input_failure(vehicle, start_pose, sample_step, max_heading_step_degrees)
    if input_failure is not None:
        reason, details = input_failure
        return KinematicResult(False, reason, (), details=details)

    assert vehicle.wheelbase is not None
    pose = VehiclePose(float(start_pose.x), float(start_pose.y), _normalize_heading(start_pose.heading_degrees))
    poses = [pose]
    audits: list[SegmentAudit] = []
    reverse_distance = 0.0
    minimum_radius_used: float | None = None
    radius_resolution: TurningRadiusResolution | None = None

    for index, segment in enumerate(segments):
        segment_failure = _segment_failure(segment, index)
        if segment_failure is not None:
            reason, details = segment_failure
            return KinematicResult(
                False,
                reason,
                tuple(poses),
                tuple(audits),
                reverse_distance,
                minimum_radius_used,
                details,
            )

        distance = float(segment.distance)
        steering = float(segment.steering_angle_degrees)
        radius = turn_radius_for_steering(vehicle.wheelbase, steering)
        if radius is not None and abs(distance) > _EPSILON:
            observed_radius = abs(radius)
            if require_min_turning_radius and radius_resolution is None:
                radius_resolution = rear_axle_turning_radius(
                    vehicle,
                    require_explicit_track_width=require_explicit_track_width,
                )
                if not radius_resolution.valid or radius_resolution.rear_axle_radius is None:
                    return KinematicResult(
                        False,
                        radius_resolution.reason,
                        tuple(poses),
                        tuple(audits),
                        reverse_distance,
                        minimum_radius_used,
                        {
                            "segment_index": index,
                            "turning_radius_resolution": radius_resolution.to_record(),
                        },
                    )
            required_radius = radius_resolution.rear_axle_radius if radius_resolution is not None else None
            if required_radius is not None and observed_radius + _EPSILON < required_radius:
                return KinematicResult(
                    False,
                    "turning_radius_below_minimum",
                    tuple(poses),
                    tuple(audits),
                    reverse_distance,
                    minimum_radius_used,
                    {
                        "segment_index": index,
                        "observed_radius": observed_radius,
                        "required_radius": required_radius,
                        "turning_radius_resolution": radius_resolution.to_record(),
                    },
                )
            minimum_radius_used = (
                observed_radius
                if minimum_radius_used is None
                else min(minimum_radius_used, observed_radius)
            )

        if distance < 0.0:
            reverse_distance += abs(distance)
            if vehicle.max_reverse_distance is not None and reverse_distance > vehicle.max_reverse_distance + _EPSILON:
                return KinematicResult(
                    False,
                    "maximum_reverse_distance_exceeded",
                    tuple(poses),
                    tuple(audits),
                    reverse_distance,
                    minimum_radius_used,
                    {
                        "segment_index": index,
                        "observed_reverse_distance": reverse_distance,
                        "maximum_reverse_distance": vehicle.max_reverse_distance,
                    },
                )

        curvature = 0.0 if radius is None else 1.0 / radius
        heading_change = curvature * distance
        audits.append(
            SegmentAudit(
                index=index,
                label=segment.label,
                direction=segment.direction,
                distance=distance,
                steering_angle_degrees=steering,
                turn_radius=radius,
                heading_change_degrees=math.degrees(heading_change),
            )
        )
        step_count = max(
            1,
            math.ceil(abs(distance) / sample_step),
            math.ceil(abs(math.degrees(heading_change)) / max_heading_step_degrees),
        )
        sub_distance = distance / step_count
        for _ in range(step_count):
            pose = _advance_pose(pose, sub_distance, curvature)
            poses.append(pose)

    details: dict[str, Any] = {
        "maneuver_model": "kinematic_bicycle_constant_steering_v1",
        "sampling": {
            "maximum_distance_step": sample_step,
            "maximum_heading_step_degrees": max_heading_step_degrees,
        },
    }
    if radius_resolution is not None:
        details["turning_radius_resolution"] = radius_resolution.to_record()
    return KinematicResult(
        True,
        None,
        tuple(poses),
        tuple(audits),
        reverse_distance,
        minimum_radius_used,
        details,
    )


def _input_failure(
    vehicle: VehicleSpec,
    start_pose: VehiclePose,
    sample_step: float,
    max_heading_step_degrees: float,
) -> tuple[str, dict[str, Any]] | None:
    numeric_pose = (start_pose.x, start_pose.y, start_pose.heading_degrees)
    if not all(math.isfinite(value) for value in numeric_pose):
        return "invalid_start_pose", {}
    if not math.isfinite(vehicle.length) or not math.isfinite(vehicle.width) or vehicle.length <= 0 or vehicle.width <= 0:
        return "invalid_vehicle_dimensions", {"length": vehicle.length, "width": vehicle.width}
    if vehicle.wheelbase is None or not math.isfinite(vehicle.wheelbase) or vehicle.wheelbase <= 0.0:
        return "vehicle_wheelbase_missing_or_invalid", {"wheelbase": vehicle.wheelbase}
    if vehicle.wheelbase > vehicle.length + _EPSILON:
        return "vehicle_wheelbase_exceeds_length", {"wheelbase": vehicle.wheelbase, "length": vehicle.length}
    if vehicle.min_turning_radius is not None and (
        not math.isfinite(vehicle.min_turning_radius) or vehicle.min_turning_radius <= 0.0
    ):
        return "invalid_minimum_turning_radius", {"min_turning_radius": vehicle.min_turning_radius}
    if not math.isfinite(vehicle.swept_path_margin) or vehicle.swept_path_margin < 0.0:
        return "invalid_swept_path_margin", {"swept_path_margin": vehicle.swept_path_margin}
    if vehicle.max_reverse_distance is not None and (
        not math.isfinite(vehicle.max_reverse_distance) or vehicle.max_reverse_distance < 0.0
    ):
        return "invalid_maximum_reverse_distance", {"max_reverse_distance": vehicle.max_reverse_distance}
    if not math.isfinite(sample_step) or sample_step <= 0.0:
        return "invalid_sample_step", {"sample_step": sample_step}
    if not math.isfinite(max_heading_step_degrees) or max_heading_step_degrees <= 0.0:
        return "invalid_heading_sample_step", {"max_heading_step_degrees": max_heading_step_degrees}
    return None


def _segment_failure(segment: MotionSegment, index: int) -> tuple[str, dict[str, Any]] | None:
    if not math.isfinite(segment.distance):
        return "invalid_motion_distance", {"segment_index": index, "distance": segment.distance}
    if not math.isfinite(segment.steering_angle_degrees) or abs(segment.steering_angle_degrees) >= 90.0:
        return "invalid_steering_angle", {
            "segment_index": index,
            "steering_angle_degrees": segment.steering_angle_degrees,
        }
    return None


def _advance_pose(pose: VehiclePose, distance: float, curvature: float) -> VehiclePose:
    heading = math.radians(pose.heading_degrees)
    if abs(curvature) <= _EPSILON:
        x = pose.x + distance * math.cos(heading)
        y = pose.y + distance * math.sin(heading)
        end_heading = heading
    else:
        end_heading = heading + curvature * distance
        x = pose.x + (math.sin(end_heading) - math.sin(heading)) / curvature
        y = pose.y + (-math.cos(end_heading) + math.cos(heading)) / curvature
    return VehiclePose(x, y, _normalize_heading(math.degrees(end_heading)))


def _normalize_heading(value: float) -> float:
    normalized = float(value) % 360.0
    return 0.0 if math.isclose(normalized, 360.0, abs_tol=_EPSILON) else normalized
