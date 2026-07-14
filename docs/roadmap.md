# Roadmap

The near-term objective is not to add more risk metrics. It is to make the
existing supported template generator predictable, fail-closed, testable, and
honest about what a “valid” result means.

## v0.2 closure baseline

The first reliability pass was pushed as the v0.2 template-planner baseline:

- final validity is checked before export and hard rejection is fail-closed;
- declared aisle and entrance graph links require plausible geometric contact;
- promoted candidates are rebuilt as official objects and the exact exported
  layout is revalidated and rescored;
- CLI errors, XML escaping, and transactional output-set writes are covered by
  end-to-end tests;
- runtime dependencies no longer include unused plotting/optimizer packages;
  and
- documentation plus Python 3.10/3.12 lint, coverage, build, and wheel-smoke CI
  define the engineering baseline.

That baseline is a **template planner**, not an automatic compliance or
construction-design system. Package licensing, Schema, version metadata, and
representative fixtures are completed in the v0.3 development line.

## v0.3: Vehicle and enforced site constraints

The current v0.3 implementation adds a narrow, fail-closed engineering layer:

- audited outer-front-wheel or rear-axle-center turning-radius input, explicit
  vehicle footprint geometry, and conservative analytic fit/reverse bounds;
- an optional perpendicular-90 reverse-in template with exact
  constant-curvature bicycle poses and conservative sampled swept envelopes;
- fail-closed turning-radius, reverse-distance, centerline policy, drivable-area,
  boundary, and hard site-exclusion decisions for that template;
- hard obstacle/reserved/feature/pedestrian/fire/access-route scopes plus
  accessible/EV minimum quotas on the final layout;
- separate advisory/project-policy/jurisdictional authority and execution
  priority, with source/effective-date metadata required for jurisdictional
  declarations; and
- MIT licensing, a packaged JSON Schema, and synthetic representative
  real-site-shaped regression inputs.

Remaining validation/product work is intentionally not hidden inside the v0.3
claim:

- permissioned/anonymized survey cases with recorded human CAD comparisons and
  tolerances;
- exact templates or general path search for angled, parallel, T-end,
  multi-point, articulated, and emergency-vehicle maneuvers;
- route connectivity/usability and accessible/EV equipment design checks; and
- sourced regional rule implementations, review/sign-off, and supported release
  operations.

The release claim and fail-closed behavior are defined in the
[v0.3 vehicle and enforced-constraint contract](v0.3_vehicle_and_constraints.md).
Representative fixtures are useful regression inputs, but are not field
validation or real customer sites.

## v0.4: Global candidate optimization

Replace heuristic-only selection without losing the current explainable
baseline:

- express aisle, connector, turnaround, and parking-module options as discrete
  candidates with conflicts and dependencies;
- add an optional OR-Tools CP-SAT backend while retaining the greedy selector as
  a fast baseline;
- support deterministic seeds, time limits, objective bounds/gaps, and solver
  provenance in the report; and
- benchmark quality and runtime on the real-site corpus before changing the
  default backend.

The `optimizer` installation extra is reserved for this phase; the current
runtime does not depend on OR-Tools.

## Later product integration

Only after the earlier release gates are met:

- multiple coordinated entrances/exits, one-way/narrow aisle strategies,
  passing-bay synthesis, arbitrary junctions, and general loops;
- DXF/site-survey import and coordinate-system handling;
- interactive editing with constraint-aware regeneration;
- versioned regional rule profiles with traceable sources and effective dates;
  and
- review/sign-off workflows, audit trails, packaging, and supported releases.

## Prioritization rule

For every new feature, prefer this order:

1. define the enforceable invariant and failure behavior;
2. add a minimal fixture that fails without the feature;
3. implement and revalidate the exact exported layout;
4. expose diagnostics and provenance; and
5. only then expand candidate generation or scoring.

This keeps “more layouts” from outrunning “layouts we can trust within the
documented boundary.”
