from __future__ import annotations

from dataclasses import asdict
from typing import Any

from openparkcad.models import SiteSpec


def build_input_diagnostics(site: SiteSpec) -> dict[str, Any]:
    active_fields = [
        "name",
        "units",
        "site.boundary polygon",
        "site.obstacles polygon",
        "parking.active stall width/length",
        "parking.active stall allowed_angles",
        "aisles.selected width",
        "constraints.setbacks.site_boundary",
    ]
    if site.entrances:
        active_fields.append("entrances drawn in diagnostics")

    parsed_future_fields: list[str] = []
    warnings: list[str] = []

    if site.standards:
        parsed_future_fields.append("standards")
        warnings.append("standards are parsed as metadata only; legal compliance is not checked yet")
    if site.entrances:
        parsed_future_fields.append("entrances")
        warnings.append("entrances are parsed and drawn, but aisle connectivity to entrances is not enforced yet")
    if site.vehicle:
        parsed_future_fields.append("vehicles.design_vehicle")
        warnings.append("vehicle dimensions and turning radius are parsed but not enforced by maneuver checks yet")
    if site.site_features:
        parsed_future_fields.append("site_features")
        warnings.append("site_features are parsed but not used for clearance or collision checks yet")
    if site.pedestrian_and_emergency:
        parsed_future_fields.append("pedestrian_and_emergency")
        warnings.append("pedestrian and emergency reservations are parsed but not enforced yet")
    if site.stall.drive_over or site.stall.blocked_sides or site.stall.access_sides != ("front",):
        parsed_future_fields.append("parking.stall access behavior")
        warnings.append("stall access_sides, blocked_sides, and drive_over are parsed but not enforced yet")
    if site.aisle_classes:
        parsed_future_fields.append("aisles.classes")
    if site.aisle_selection_mode != "fixed":
        warnings.append("aisle selection modes beyond fixed are documented but not optimized yet")
    if site.constraints.get("maneuvering"):
        parsed_future_fields.append("constraints.maneuvering")
        warnings.append("maneuvering constraints are parsed but not enforced yet")
    if site.optimization:
        parsed_future_fields.append("optimization")
        warnings.append("optimization weights are parsed but the current generator is still greedy")

    return {
        "source_format": site.source_format,
        "active_fields": active_fields,
        "parsed_future_fields": sorted(set(parsed_future_fields)),
        "warnings": warnings,
        "field_support": _field_support(site),
        "constraint_status": _constraint_status(site),
        "entrances": [asdict(item) for item in site.entrances],
        "vehicle": asdict(site.vehicle) if site.vehicle else None,
        "aisle_selection": {
            "mode": site.aisle_selection_mode,
            "fixed_class": site.fixed_aisle_class,
            "effective_width": site.aisle_width,
        },
        "active_stall_type": asdict(site.stall),
    }


def _constraint_status(site: SiteSpec) -> list[dict[str, str]]:
    return [
        {
            "constraint": "geometry containment",
            "status": "active",
            "note": "Generated stall and aisle access polygons are checked against usable area.",
        },
        {
            "constraint": "obstacle avoidance",
            "status": "active",
            "note": "Polygon obstacles are removed from the usable area.",
        },
        {
            "constraint": "entrance connectivity",
            "status": "future",
            "note": "Entrances are parsed and drawn but not connected to aisle graph yet.",
        },
        {
            "constraint": "vehicle turning radius",
            "status": "future",
            "note": "Vehicle data is parsed but swept path and turning checks are not implemented yet.",
        },
        {
            "constraint": "narrow two-way deadlock",
            "status": "future",
            "note": "Narrow two-way aisles should remain disabled until graph deadlock checks exist.",
        },
        {
            "constraint": "pedestrian and emergency reservations",
            "status": "future",
            "note": "Reserved routes are parsed but not enforced as no-parking areas yet.",
        },
    ]


def _field_support(site: SiteSpec) -> dict[str, str]:
    support = {
        "version": "active",
        "name": "active",
        "units": "active",
        "standards": "parsed_not_enforced",
        "site.boundary.polygon": "active",
        "site.boundary.curve_loop": "future",
        "site.obstacles.polygon": "active",
        "site.reserved_areas": "future",
        "site_features": "drawn_not_enforced" if site.site_features else "future",
        "entrances": "drawn_not_enforced" if site.entrances else "future",
        "pedestrian_and_emergency.pedestrian_routes": _pedestrian_status(site, "pedestrian_routes"),
        "pedestrian_and_emergency.fire_lanes": _pedestrian_status(site, "fire_lanes"),
        "vehicles.design_vehicle": "parsed_not_enforced" if site.vehicle else "future",
        "parking.standard_perpendicular": "active",
        "parking.angled_parallel_t_end": "future",
        "parking.drive_over": "parsed_not_enforced",
        "parking.access_sides": "parsed_not_enforced",
        "parking.blocked_sides": "parsed_not_enforced",
        "aisles.fixed_wide_two_way": "active",
        "aisles.narrow_one_way": "future",
        "aisles.narrow_two_way": "future",
        "constraints.geometry_containment": "active",
        "constraints.entrance_connectivity": "future",
        "constraints.turning_radius": "future",
        "constraints.swept_path": "future",
        "optimization.weights": "parsed_not_enforced" if site.optimization else "future",
        "diagnostics.report": "active",
        "diagnostics.debug_layers": "active",
    }
    return support


def _pedestrian_status(site: SiteSpec, key: str) -> str:
    if site.pedestrian_and_emergency.get(key):
        return "drawn_not_enforced"
    return "future"
