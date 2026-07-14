# OpenParkCAD

OpenParkCAD is a Python-first experimental kernel for generating parking layouts
inside irregular land parcels. The package version is `0.3.0`; the
[current capability matrix](docs/current_status.md) and each generated report,
not the version number alone, define which checks are active.

This is a planning and algorithm-development tool. It is **not** a code-compliance
checker, a construction-design system, or a substitute for independent vehicle,
fire-access, accessibility, and local-authority review.

## Current capability

The solver can currently:

- read the documented JSON site model and separate active fields from parsed,
  future-facing fields;
- generate a straight entrance-connected main aisle, end turnaround,
  perpendicular branches, and a limited same-side U-shaped connector pattern;
- generate 90-degree and angled stalls on supported main/branch aisles, and
  conservative 90-degree stalls on connectors;
- enforce hard scoped clearances for polygonal obstacles, reserved areas,
  supported site features, and pedestrian/fire/access-route geometry;
- validate generated aisle/stall reachability with a traffic graph;
- apply rectangular and optional L-shaped maneuver-clearance **proxies**;
- when requested, resolve a design vehicle's turning radius to the rear-axle
  path and run either conservative analytic checks or the supported
  perpendicular-90 reverse-in swept-path template;
- enforce caller-declared accessible/EV minimum counts from explicit stall-type
  classifications;
- compare stall types and main/branch stall assignments with an explainable
  score;
- create a candidate snapshot, conflict matrix, heuristic shadow selection,
  validated preview layout, and guarded preview promotion;
- report Phase 5Q operational-risk **proxies** for junctions, entrance throats,
  routes, directionality, narrow two-way aisles, passing bays, meeting gaps, and
  junction merges; and
- export layered DXF, SVG preview, and a detailed JSON report.

The phase label describes implementation history, not product readiness. See
[the current capability matrix](docs/current_status.md) for the precise trust
boundary and [the roadmap](docs/roadmap.md) for release priorities.

## Setup

OpenParkCAD requires Python 3.10 or newer. Conda is not required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Runtime installation does not require Matplotlib or OR-Tools. The current
selector is heuristic and does not call OR-Tools. The reserved optimizer extra
is available only for future CP-SAT work:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,optimizer]"
```

## Quick start

```powershell
.\.venv\Scripts\openparkcad.exe solve examples/phase0_site.json `
  --out output/layout.dxf `
  --preview output/layout.svg `
  --report output/report.json
```

The equivalent module entry point is `python -m openparkcad solve ...`.

The JSON report includes the selected layout and score, attempted candidates,
input diagnostics, traffic-graph validation, maneuver/vehicle validation,
`site_constraint_validation`, the versioned combined `engineering_validation`,
`candidate_snapshot`, `candidate_network_preview`, `candidate_layout_preview`,
`candidate_layout_promotion`, and Phase 5Q `operational_quality`.

## Selection and promotion

The official layout starts from a conservative template:

```text
entry-capable entrance -> straight two-way main aisle -> end turnaround
                                  |
                                  +-> optional perpendicular branches
                                  +-> optional same-side U connector
```

Heading/entrance-offset candidates and supported stall assignments are scored.
Branch and connector candidates are selected with deterministic heuristics, not
global mathematical optimization.

The candidate layout is always reported as a preview. Replacing the official
DXF/SVG/report layout is opt-in and defaults to disabled:

```json
{
  "optimization": {
    "promote_candidate_layout_preview": true
  }
}
```

Promotion occurs only when the preview passes its configured geometry,
maneuver/vehicle, site-constraint/quota, graph, and operational gates and is not
worse under the current score.
The bundled example deliberately enables promotion so the preview path remains
exercised; other inputs must opt in explicitly.

## Validation modes and trust boundary

Vehicle-level checks are opt-in under `constraints.maneuvering`:

- `require_turning_radius_check: true` with swept path disabled uses
  `active_conservative` mode. It audits the input-radius reference, converts an
  outer-front-wheel radius to a rear-axle-center radius, checks vehicle/stall
  fit, and applies a conservative reverse-distance upper bound.
- `require_swept_path_check: true` uses `active_exact` mode for supported
  perpendicular-90 stalls. It integrates a constant-curvature low-speed bicycle
  path exactly, then checks a conservative sampled body envelope against the
  drivable area, site boundary, centerline policy, and hard exclusions.

Both modes require auditable vehicle geometry. In particular,
`outer_front_wheel` radius input requires wheelbase and track width. A requested
check fails closed when required parameters are missing, the stall/template is
unsupported, or the check fails; the older proxy cannot silently turn that into
a pass. `active_exact` names the path integration mode, not physical-world
exactness: the current template is one reverse 90-degree constant-radius arc
plus a straight segment, with exit represented as its time reverse. It does not
model steering transients, tyres, dynamics, driver variation, or articulated
vehicles.

Hard geometry uses declared `affects`, `priority`, and `authority` semantics.
Supported obstacle/reserved/feature/route geometry can block stalls, aisles,
and/or swept paths. Advisory, soft, draw-only, and future declarations remain
non-blocking. Accessible/EV quotas are project minimums counted on the final
layout; they are not built-in statutory ratios or accessibility certification.

Operational-quality behavior remains separately configurable.

`optimization.operational_quality_mode` supports:

- `score_only` (default): report and score Phase 5Q risks without rejecting the
  official layout;
- `promotion_gate`: keep the risks soft for the current layout but block preview
  promotion when the configured risk limit is exceeded; and
- `hard_reject`: treat a configured operational-risk limit violation as an
  invalid solve and do not publish official output artifacts.

Set `optimization.operational_max_risk_score` to establish the limit used by the
gate/reject modes.

Operational checks do not simulate traffic. Declared aisle links must make
geometric contact before they become graph edges, but the traffic graph is still a static
consistency and reachability check, and Phase 5Q uses graph/geometry indicators.
A reported pass therefore means “passes the active checks for the supported
template and declared project inputs,” not “every real vehicle can traverse the
design” or “the design complies with a code.”

## Input model

Use [examples/phase0_site.json](examples/phase0_site.json) as the executable
example and [docs/input_model.md](docs/input_model.md) as the field reference.
All currently supported dimensions are interpreted as metres.

The model intentionally accepts some fields ahead of enforcement so reports can
expose unsupported requirements instead of silently discarding them. Hard
pedestrian/fire geometry is enforced only for its declared geometric exclusion
scopes; this does not validate route continuity, width/slope rules, emergency
apparatus movement, or regulatory compliance.

## Not implemented

The current release does not provide:

- arbitrary road-network synthesis, general intersections, multiple coordinated
  entrances/exits, or a general loop optimizer;
- general vehicle-path search, multi-point maneuvers, exact swept paths for
  angled/parallel/T-end stalls, steering transients, tyre/dynamic behavior, or
  articulated/emergency-vehicle models;
- dynamic traffic, arrival/priority simulation, or capacity/queue analysis;
- pedestrian/fire/access-route connectivity and usability analysis, accessible
  stall dimensional certification, EV equipment layout, slope/drainage checks,
  or built-in local-code profiles;
- global CP-SAT/MIP optimization (the current selector is greedy/heuristic);
- DXF/site-survey import, interactive editing, or a graphical application; or
- certification that generated drawings are construction-ready or compliant.

Parallel stalls, T-end stalls, connector-side angled stalls, and non-90-degree
perpendicular stalls are also outside the active generation path.

## Development

```powershell
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest --cov=openparkcad --cov-report=term-missing
.\.venv\Scripts\python.exe -m build
```

CI runs lint, tests with branch coverage, and an installed-wheel CLI smoke test
on Python 3.10 and 3.12.

## Design documents

- [Changelog](CHANGELOG.md)
- [Current status and capability matrix](docs/current_status.md)
- [Roadmap](docs/roadmap.md)
- [Input model](docs/input_model.md)
- [v0.3 vehicle and enforced-constraint contract](docs/v0.3_vehicle_and_constraints.md)
- [Algorithm design discussion](docs/algorithm_design_discussion.md)
- [Detailed phased implementation history](docs/phased_plan.md)

## License

OpenParkCAD is available under the [MIT License](LICENSE).
