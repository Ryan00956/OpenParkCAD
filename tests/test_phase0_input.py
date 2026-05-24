import json
from pathlib import Path

from openparkcad.diagnostic_geometry import pedestrian_emergency_shapes, site_feature_shapes
from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.models import site_from_dict


def test_phase0_example_parses_core_fields():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))

    site = site_from_dict(data)

    assert site.source_format == "phase0"
    assert site.name == "phase0 realistic input sample"
    assert site.entrances[0].id == "main-gate"
    assert site.vehicle.id == "passenger-car"
    assert site.stall.id == "standard-90"
    assert site.aisle_width == 6.0
    assert site.candidate_angles == (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)
    assert len(site.site_features) == 2


def test_phase0_diagnostics_are_honest_about_future_fields():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)

    diagnostics = build_input_diagnostics(site)

    assert diagnostics["source_format"] == "phase0"
    assert "site_features" in diagnostics["parsed_future_fields"]
    assert diagnostics["field_support"]["site_features"] == "drawn_not_enforced"
    assert diagnostics["field_support"]["entrances"] == "drawn_not_enforced"
    assert diagnostics["field_support"]["vehicles.design_vehicle"] == "parsed_not_enforced"
    assert any("turning radius" in item for item in diagnostics["warnings"])
    assert any(item["constraint"] == "entrance connectivity" and item["status"] == "future" for item in diagnostics["constraint_status"])


def test_phase0_diagnostic_shapes_are_available_for_export():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)

    feature_shapes = site_feature_shapes(site.site_features)
    pedestrian_shapes = pedestrian_emergency_shapes(site.pedestrian_and_emergency)

    assert {shape.id for shape in feature_shapes} == {"column-a1", "charger-demo"}
    assert {shape.layer for shape in feature_shapes} == {"SITE_FEATURES"}
    assert {shape.id for shape in pedestrian_shapes} == {"walkway-demo"}
    assert {shape.layer for shape in pedestrian_shapes} == {"PEDESTRIAN"}


def test_legacy_example_still_parses():
    data = json.loads(Path("examples/simple_lot.json").read_text(encoding="utf-8"))

    site = site_from_dict(data)

    assert site.source_format == "legacy"
    assert site.stall.width == 2.5
    assert site.candidate_angles == (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)
