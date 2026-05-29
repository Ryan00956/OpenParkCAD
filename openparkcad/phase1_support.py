from __future__ import annotations

from openparkcad.models import AisleClassSpec, EntranceSpec, SiteSpec


def entry_capable_entrances(site: SiteSpec) -> list[EntranceSpec]:
    return [
        entrance
        for entrance in site.entrances
        if entrance.mode in {"shared", "entry_only"} or "enter" in entrance.allowed_movements
    ]


def supports_phase1_aisle(site: SiteSpec) -> bool:
    if site.aisle_selection_mode != "fixed":
        return False
    fixed_class = fixed_aisle_class(site)
    if fixed_class is None:
        return False
    return is_phase1_aisle_class(fixed_class)


def supports_phase1_stall(site: SiteSpec) -> bool:
    stall = site.stall
    base_supported = not stall.drive_over and "front" in stall.access_sides and "front" not in stall.blocked_sides
    if not base_supported:
        return False
    if stall.family == "perpendicular":
        return module_angle_allowed(90.0, stall.allowed_angles)
    if stall.family == "angled":
        return angled_module_angle(stall.allowed_angles) is not None
    return False


def phase1_unsupported_inputs(site: SiteSpec) -> list[dict[str, str]]:
    """Return Phase 1 support notes for accepted-but-not-generated inputs."""
    issues: list[dict[str, str]] = []
    fixed_class = fixed_aisle_class(site)
    if site.aisle_selection_mode != "fixed":
        issues.append(
            {
                "field": "aisles.selection_mode",
                "value": site.aisle_selection_mode,
                "reason": "Phase 1 only generates a fixed wide two-way aisle class.",
            }
        )
    if fixed_class is None:
        issues.append(
            {
                "field": "aisles.fixed_class",
                "value": str(site.fixed_aisle_class),
                "reason": "Phase 1 needs a resolvable fixed aisle class.",
            }
        )
    elif not is_phase1_aisle_class(fixed_class):
        issues.append(
            {
                "field": f"aisles.classes.{fixed_class.id}",
                "value": f"capacity={fixed_class.capacity}, directionality={fixed_class.directionality}, enabled={fixed_class.enabled}",
                "reason": "Phase 1 only supports enabled two_vehicle/two_way aisle classes.",
            }
        )

    stall = site.stall
    if stall.family not in {"perpendicular", "angled"}:
        issues.append(
            {
                "field": "parking.active_stall.family",
                "value": stall.family,
                "reason": "Current generation supports standard perpendicular stalls and main-aisle angled stalls only.",
            }
        )
    if stall.family == "perpendicular" and not module_angle_allowed(90.0, stall.allowed_angles):
        issues.append(
            {
                "field": "parking.active_stall.allowed_angles",
                "value": ",".join(str(angle) for angle in stall.allowed_angles),
                "reason": "Phase 1 only places 90-degree stalls.",
            }
        )
    if stall.family == "angled" and angled_module_angle(stall.allowed_angles) is None:
        issues.append(
            {
                "field": "parking.active_stall.allowed_angles",
                "value": ",".join(str(angle) for angle in stall.allowed_angles),
                "reason": "Angled stall generation needs an angle between 0 and 90 degrees.",
            }
        )
    if stall.drive_over:
        issues.append(
            {
                "field": "parking.active_stall.drive_over",
                "value": "true",
                "reason": "Drive-over painted stalls require later maneuver/traffic logic.",
            }
        )
    if "front" not in stall.access_sides:
        issues.append(
            {
                "field": "parking.active_stall.access_sides",
                "value": ",".join(stall.access_sides),
                "reason": "Phase 1 only models aisle-facing front access.",
            }
        )
    if "front" in stall.blocked_sides:
        issues.append(
            {
                "field": "parking.active_stall.blocked_sides",
                "value": ",".join(stall.blocked_sides),
                "reason": "The aisle-facing side cannot be blocked in the Phase 1 maneuver approximation.",
            }
        )
    return issues


def is_phase1_aisle_class(aisle_class: AisleClassSpec) -> bool:
    return aisle_class.enabled and aisle_class.capacity == "two_vehicle" and aisle_class.directionality == "two_way"


def module_angle_allowed(angle: float, allowed_angles: tuple[float, ...]) -> bool:
    normalized = angle % 180
    return any(abs(normalized - (allowed % 180)) <= 1e-6 for allowed in allowed_angles)


def angled_module_angle(allowed_angles: tuple[float, ...]) -> float | None:
    for angle in allowed_angles:
        normalized = angle % 180
        if 1e-6 < normalized < 90.0 - 1e-6:
            return normalized
    return None


def fixed_aisle_class(site: SiteSpec):
    if not site.fixed_aisle_class:
        return site.aisle_classes[0] if site.aisle_classes else None
    for aisle_class in site.aisle_classes:
        if aisle_class.id == site.fixed_aisle_class:
            return aisle_class
    return None
