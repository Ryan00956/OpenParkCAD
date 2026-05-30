# OpenParkCAD

OpenParkCAD is a Python-first experiment for automatically laying out parking
spaces inside irregular land parcels.

The current MVP uses Shapely for geometry and ezdxf for CAD output. It can:

- read the Phase 0+ JSON input model,
- draw diagnostic layers for entrances, fixed features, and pedestrian/fire reservations,
- generate a conservative Phase 1 layout from an entrance-connected main aisle,
- build a Phase 2A traffic graph from the generated aisles and stalls,
- run a Phase 3A conservative stall access envelope check,
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

When same-side branches are selected, the solver may add a connector aisle
between adjacent branch ends. This can form a simple U-shaped loop when the
extra aisle reduces the dead-end penalty enough to offset any lost stalls or
added aisle area. Once a connector is accepted, the endpoint branch turnaround
pads are removed because those branches are no longer dead ends. The connector
can also serve conservative 90-degree stalls along its sides when there is room
after leaving a clear throat at both junctions.

Branch start positions are auto-sampled by default. You can also control the
sampling with:

```json
"optimization": {
  "branch_start_step": 2.5,
  "branch_sides": ["left", "right"],
  "max_branches": 2,
  "enable_connectors": true,
  "connector_throat_length": 6.0
}
```

The report includes branch candidate reasons such as
`branch_too_short_for_turnaround`, `branch_overlaps_existing_layout`, and
`branch_does_not_improve_score`. Selected branches are listed in
`selected_branches`; selected connector aisles are listed in
`selected_connectors`, including removed turnaround pads and any stalls added
along the connector.

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

Phase 3A adds a conservative maneuver-access check. For each generated stall,
the solver finds the stall edge facing its serving aisle and projects a
rectangular access envelope into the aisle. The envelope must be covered by
drivable aisle geometry and stay inside the usable site area. Invalid stalls are
filtered before graph validation and scoring.

Phase 3B adds a conservative turning-sweep proxy. It expands that stall-front
envelope along the aisle direction on both sides, so side obstacles or aisle-end
clips can invalidate a stall even when the immediate front rectangle is clear.

Phase 3C-1 routes stalls through explicit maneuver rules. The active rule is
currently `perpendicular_90_proxy`. Phase 3C-2 also adds an active
`angled_proxy` validator rule for angled stall geometry. Phase 3D-1 can
generate angled stalls along the main aisle, and Phase 3D-2 extends angled
generation to branch aisles. Connector-side angled stalls, parallel stalls,
T-end stalls, and non-90 perpendicular stalls remain future work and are
reported with explicit reasons if encountered.

Phase 3E compares enabled stall type candidates. If the input JSON enables more
than one stall type, the solver tries each candidate independently, runs the
same geometry, maneuver, graph, and scoring pipeline, then selects the
highest-scoring graph-valid layout. The report includes
`selected_stall_type_id` and `stall_type_attempts` so the choice is visible.

Phase 3F-1 compares stall type assignments by aisle role. With multiple enabled
stall types, it now enumerates `main_stall_type x branch_stall_type`, tags every
generated stall with `stall_type_id`, validates each stall with the matching
maneuver rule, and reports `selected_stall_assignment` plus
`stall_assignment_attempts`.

Phase 4A-1 adds a candidate-object snapshot layer. The solver now reports
selected aisle/stall objects plus evaluated main, branch, and connector
candidates in `candidate_snapshot`. This is the data layer for the later
conflict matrix and optimizer; it does not yet change the selected layout.
Phase 4A-2 adds that first conflict matrix: candidate objects with geometry now
record overlapping object ids in `conflict_ids`, and the report includes
`candidate_snapshot.conflict_matrix`.
Phase 4B-1 adds a report-only shadow selector over branch and connector
candidates. It uses the conflict matrix to choose a compatible candidate set and
stores the result in `candidate_snapshot.selection`; it does not yet replace the
current generated layout.

You can tune these maneuver checks with:

```json
"optimization": {
  "maneuver_access_depth": 6.0,
  "maneuver_access_coverage_ratio": 0.95,
  "maneuver_turn_buffer_length": 2.5,
  "maneuver_turn_coverage_ratio": 0.95,
  "maneuver_angled_access_depth": 5.2,
  "maneuver_angled_turn_buffer_length": 1.25
}
```

This is still conservative. It does not yet build arbitrary loops,
intersections, narrow aisles, exact steering arcs, or swept-path turning checks.

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
aisles and stalls, but it does not yet generate arbitrary road networks,
intersections, narrow aisles, steering swept paths, turning-radius validation,
fire access validation, slopes, local code profiles, accessible stalls, or
per-stall mixed parking module optimization.

## Design Notes

- [Algorithm design discussion](docs/algorithm_design_discussion.md)
- [Phased implementation plan](docs/phased_plan.md)
