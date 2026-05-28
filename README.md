# OpenParkCAD

OpenParkCAD is a Python-first experiment for automatically laying out parking
spaces inside irregular land parcels.

The current MVP uses Shapely for geometry and ezdxf for CAD output. It can:

- read the Phase 0+ JSON input model,
- draw diagnostic layers for entrances, fixed features, and pedestrian/fire reservations,
- generate a conservative Phase 1 layout from an entrance-connected main aisle,
- build a Phase 2A traffic graph from the generated aisles and stalls,
- reserve an end turnaround pad,
- avoid obstacles,
- write a DXF file with CAD layers,
- write an SVG preview,
- write a JSON report.

## Setup

Use a normal Python virtual environment. Conda is not required for this project.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Quick Start

```powershell
.\.venv\Scripts\python.exe -m openparkcad solve examples/phase0_site.json --out output/phase1_site.dxf --preview output/phase1_site.svg --report output/phase1_site_report.json
```

Open `output/phase1_site.svg` in a browser for a quick preview, or open the DXF in CAD.

The report includes `input_diagnostics`, which separates active checks from
future-facing fields that are parsed but not enforced yet.

The Phase 1 generator currently uses the first supported circulation pattern:

```text
entry-capable entrance -> straight wide two-way main aisle -> end turnaround -> standard stalls on both sides
```

It tries a small set of heading offsets around the entrance direction and a few
parallel offsets inside the entrance width, then keeps the legal main-aisle
layout with the most stalls.

It may also add multiple perpendicular branches from the main aisle. Each branch
must fit inside the usable site, connect to the main aisle, avoid conflicts with
existing branch geometry, pass traffic graph validation, improve the score, and
reserve its own end turnaround.

Branch start positions are auto-sampled by default. You can also control the
sampling with:

```json
"optimization": {
  "branch_start_step": 2.5,
  "max_branches": 2
}
```

The report includes branch candidate reasons such as
`branch_too_short_for_turnaround`, `branch_overlaps_existing_layout`, and
`branch_does_not_improve_score`. Selected branches are listed in
`selected_branches`.

Layouts are selected by an explainable score, not only by stall count:

```text
score =
  stall_count value
  - aisle area penalty
  - heading deviation penalty
  - entrance offset penalty
  - branch complexity penalty
  - dead-end length penalty
```

The JSON report includes the full score breakdown.

For Phase 1 explainability, the report also lists:

- every generated aisle with its role and simple parent/entrance link,
- every generated stall with the aisle that serves it,
- unsupported Phase 1 input choices and the reason they were not generated.

The report now includes a Phase 2A `traffic_graph` section. It validates whether
generated aisles are reachable from an entrance, whether stalls reference
existing aisles, whether stalls have an exit path, and whether dead ends are
covered by turnaround pads.

Phase 2B uses that graph validation as a hard candidate filter before scoring.
Invalid candidates are skipped, and attempt diagnostics include `graph_valid`
and `graph_errors`.

This is still conservative. It does not yet build loops, intersections, narrow
aisles, or swept-path turning checks.

## Input Format

```json
{
  "version": "0.1",
  "name": "phase1 site",
  "units": "m",
  "site": {
    "boundary": {
      "type": "polygon",
      "points": [[0, 0], [64, 0], [64, 34], [50, 45], [10, 39], [0, 28]]
    },
    "obstacles": []
  },
  "entrances": [
    {
      "id": "main-gate",
      "mode": "shared",
      "center": [8, 0],
      "width": 7.0,
      "heading_degrees": 90,
      "allowed_movements": ["enter", "exit"]
    }
  ],
  "parking": {
    "stall_types": [
      {
        "id": "standard-90",
        "family": "perpendicular",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [90],
        "enabled": true
      }
    ]
  },
  "aisles": {
    "selection_mode": "fixed",
    "fixed_class": "wide-two-way-no-cross",
    "classes": [
      {
        "id": "wide-two-way-no-cross",
        "width": 6.0,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "forbidden",
        "enabled": true
      }
    ]
  }
}
```

All dimensions are interpreted as meters.

## Current Limitations

This is still an early algorithmic kernel. It now builds a graph for generated
aisles and stalls, but it does not yet generate loops, intersections, narrow
aisles, turning swept paths, fire access validation, slopes, local code
profiles, accessible stalls, or mixed parking modules.

## Design Notes

- [Algorithm design discussion](docs/algorithm_design_discussion.md)
- [Phased implementation plan](docs/phased_plan.md)
