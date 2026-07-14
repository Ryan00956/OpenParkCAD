import json
from pathlib import Path

from openparkcad.diagnostics import build_input_diagnostics
from openparkcad.generator import generate_layout
from openparkcad.models import site_from_dict


def test_generated_layout_reports_scoring_weights_as_active():
    data = json.loads(Path("examples/phase0_site.json").read_text(encoding="utf-8"))
    site = site_from_dict(data)
    diagnostics = build_input_diagnostics(site, generate_layout(site))

    assert diagnostics["field_support"]["optimization.weights"] == "active"

