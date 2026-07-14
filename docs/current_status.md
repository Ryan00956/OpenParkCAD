# Current status

OpenParkCAD is an executable `0.3.0` algorithm prototype. Its historical phase
labels and package version are not capability claims: the trustworthy product
boundary is the set of checks reported as active for the exact official layout.
The generator remains limited to a small family of parking-network templates
that it exports for engineering inspection.

## Capability matrix

| Area | Current status | Boundary |
| --- | --- | --- |
| Input | Versioned JSON shape plus machine-readable Schema; boundary, obstacles/reservations, entrances, vehicle, stall/aisle definitions, scoped constraints, quotas, optimization | Schema acceptance is not enforcement; runtime diagnostics remain authoritative |
| Usable geometry | Polygonal site/setback, polygon obstacles, and supported polygon/circle/rectangle/polyline-buffer hard exclusions with clearance and scope | No survey/DXF import, curved boundary, slope, drainage, or topology-repair workflow |
| Circulation generation | Straight entrance-connected main aisle, end turnarounds, perpendicular branches, limited same-side U connector | No arbitrary graph, general junction synthesis, or multi-entrance coordination |
| Parking generation | Supported 90-degree and angled main/branch stalls; conservative connector-side 90-degree stalls | No parallel, T-end, connector-side angled, or general mixed per-stall module optimization |
| Candidate selection | Scored template attempts, conflict snapshot, dependency-aware heuristic shadow selection | Greedy/heuristic, not globally optimal; OR-Tools is not called |
| Preview promotion | Full candidate preview, validation, score comparison, guarded replacement of official layout | Explicit opt-in; disabled by default |
| Geometry validation | Containment, scoped hard obstacle/reserved/feature/route conflicts, serving-aisle association | `authority=advisory` or advisory/soft/draw-only/future priority is intentionally non-blocking |
| Traffic graph | Geometric contact for declared links, aisle/stall reachability, entrance/exit path, dead-end turnaround checks | Static consistency proxy, not traffic simulation or proof of capacity |
| Maneuver validation | Legacy rectangular/L/angled proxies plus requested vehicle checks | Exact vehicle template is perpendicular-90 reverse-in only; other supported generation still uses documented proxies unless a hard vehicle request rejects it |
| Vehicle validation | Audited outer-front-wheel/rear-axle radius handling, conservative analytic mode, optional exact constant-curvature bicycle path with conservative body envelope | No general path search, steering/tyre/dynamics model, articulated vehicle, or independent swept-path certification |
| Quotas | Positive accessible/EV minimums counted on final stalls through explicit classifications or charging features | Project-policy counts only; no statutory ratio, dimensional accessibility, equipment-placement, or route-usability certification |
| Operational quality | Phase 5Q junction, entrance, route, directionality, narrow-two-way, passing-bay, meeting, and merge risks | Geometry/graph proxies; no arrival, priority, queue, or conflict simulation |
| Output | Layered DXF, SVG preview/debug overlay, detailed JSON diagnostics, versioned combined engineering decision | No interactive CAD editing or construction-document workflow |

## Decision semantics

Candidate layout promotion is controlled by
`optimization.promote_candidate_layout_preview`. The default is `false`; the
bundled example sets it to `true` to exercise the promotion path. Without the
flag, the candidate layout remains a report/debug preview and cannot replace the
official output geometry.

Operational quality has three modes:

| Mode | Effect |
| --- | --- |
| `score_only` | Adds risks to the report/score and keeps the layout eligible |
| `promotion_gate` | Blocks preview promotion when risk exceeds the configured limit |
| `hard_reject` | Makes an over-limit solve invalid and prevents official artifact publication |

All three modes operate on Phase 5Q proxies. `hard_reject` strengthens software
decision semantics; it does not turn those proxies into regulatory validation.

Vehicle and site policies are independent hard gates. When a turning-radius,
swept-path, or reverse-distance check is requested, missing vehicle inputs,
unsupported maneuver templates, or a failed check invalidate that candidate.
Hard site definitions, exact-layout conflicts, required routes, and positive
accessible/EV quotas are also fail-closed. Candidate promotion and final CLI
publication use the same vehicle/site gates; no official output set is replaced
after an invalid decision.

The JSON `engineering_validation` block is the combined v0.3 decision surface.
It records contract/algorithm versions, exact-layout object IDs, active,
advisory, unsupported, and failed rules, authority metadata, per-stall vehicle
evidence, site conflicts, and quota counts. Nested evidence remains available
for audit; this summary does not weaken any hard gate.

`active_exact` means that constant-curvature bicycle poses are integrated
exactly for the supported reverse-in template. Collision checking deliberately
uses a conservative envelope between sampled body poses. `active_conservative`
uses radius conversion, vehicle/stall fit, and a reverse-distance upper bound;
it does not prove a spatial path or centerline crossing.

## Reliability closure in the current tree

The v0.2 closure and current v0.3 development tree close the highest-risk
software gaps that were present in the original prototype:

- declared parent/connector/entrance relationships no longer create usable graph
  edges when the corresponding generated geometry is physically disconnected;
- an operational `hard_reject`, invalid graph, invalid maneuver/vehicle/site
  result, unmet quota, or empty layout is rejected before any official artifact
  is published;
- DXF, SVG, and JSON are committed as one output set, with existing artifacts
  preserved if writing fails;
- promoted candidates are rebuilt with official IDs, revalidated, rescored, and
  reflected consistently in snapshots and provenance;
- user-controlled SVG text is XML-escaped; and
- lint, branch coverage, package build, and installed-wheel execution are
  represented in the Python 3.10/3.12 CI baseline.

The v0.3 branch adds:

- audited turning-radius reference conversion and fail-closed vehicle inputs;
- optional perpendicular-90 low-speed bicycle/template validation with
  conservative swept-envelope collision checks;
- scoped hard exclusions, rule authority/priority reporting, required-route
  definition checks, and accessible/EV quota enforcement; and
- MIT licensing, a packaged JSON Schema, and synthetic representative
  real-site-shaped fixtures.

These changes expand engineering checks inside a narrow documented template.
They do not establish statutory compliance or field-validated vehicle access.

## Release posture

The project is suitable for:

- algorithm experiments and regression fixtures;
- explainable comparison of supported template layouts;
- project-policy screening with explicitly declared vehicle and exclusion data;
- DXF/SVG review by a knowledgeable human; and
- developing stronger geometry, vehicle, and optimization layers.

It is not suitable for unattended production design, permit/code claims,
construction output, or safety-critical acceptance. Those claims remain blocked
by general vehicle-path coverage, permissioned real-site/human-CAD comparison,
regional-rule, and product-integration work in the [roadmap](roadmap.md).

## Verification baseline

The repository baseline is intentionally reproducible rather than tied to a
stale test-count claim:

```powershell
ruff check .
python -m pytest --cov=openparkcad --cov-report=term-missing
python -m build
```

CI executes the same lint/test/build path and runs the installed wheel against
the bundled example on Python 3.10 and 3.12.

## v0.3 acceptance boundary

The vehicle-validity work is governed by the
[v0.3 vehicle and enforced-constraint contract](v0.3_vehicle_and_constraints.md).
The implemented code covers the contract's narrow vehicle and project-policy
core, but a constraint is active only when the report says so and records the
result for the exact official layout. Accepted or drawn input vocabulary is not
evidence of enforcement.

The representative inputs under `tests/fixtures/v0_3/` are synthetic regression
assets shaped like common real sites. They are not customer surveys or completed
field validation, and they do not replace the human CAD comparison gate.
