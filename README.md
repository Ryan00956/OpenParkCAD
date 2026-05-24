# OpenParkCAD

OpenParkCAD is a Python-first experiment for automatically laying out parking
spaces inside irregular land parcels.

The current MVP uses Shapely for geometry and ezdxf for CAD output. It can:

- read a JSON site boundary,
- try multiple parking angles,
- generate stalls with clear aisle access strips,
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
.\.venv\Scripts\python.exe -m openparkcad solve examples/simple_lot.json --out output/simple.dxf --preview output/simple.svg --report output/simple_report.json
```

Open `output/simple.svg` in a browser for a quick preview, or open the DXF in CAD.

To try the Phase 0 input model with entrances, vehicle data, aisle classes,
site features, and diagnostics:

```powershell
.\.venv\Scripts\python.exe -m openparkcad solve examples/phase0_site.json --out output/phase0_site.dxf --preview output/phase0_site.svg --report output/phase0_site_report.json
```

The report includes `input_diagnostics`, which separates active checks from
future-facing fields that are parsed but not enforced yet.

## Input Format

```json
{
  "name": "simple lot",
  "boundary": [[0, 0], [60, 0], [60, 36], [42, 45], [0, 35]],
  "obstacles": [
    [[24, 14], [34, 14], [34, 22], [24, 22]]
  ],
  "stall": {
    "width": 2.5,
    "length": 5.3
  },
  "aisle_width": 6.0,
  "candidate_angles": [0, 15, 30, 45, 60, 75, 90],
  "margin": 0.2
}
```

All dimensions are interpreted as meters.

## Current Limitations

This is still an early algorithmic kernel. It now checks that every counted stall
has an adjacent clear aisle strip, but it does not yet prove full circulation
connectivity, turning radius, fire access, slopes, local codes, accessible stalls,
or mixed parking modules. The next serious step is replacing the row-by-row
greedy generator with a candidate-generation plus optimization pipeline.

## Design Notes

- [Algorithm design discussion](docs/algorithm_design_discussion.md)
- [Phased implementation plan](docs/phased_plan.md)
