# Roadmap

The near-term objective is not to add more risk metrics. It is to make the
existing supported template generator predictable, fail-closed, testable, and
honest about what a “valid” result means.

## Current closure baseline

The first reliability pass is implemented in the current post-`0.1.0` working
tree:

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

This closes the immediate correctness issues without claiming a new vehicle or
regulatory capability.

## v0.2 release: Trustworthy template planner

Before publishing `0.2.0`:

- run the new CI matrix in the hosted environment and keep it green;
- publish a machine-readable input schema and version the report contract;
- add several anonymized real-lot fixtures, expected invariants, and DXF review
  notes instead of relying primarily on synthetic geometry;
- record runtime/quality baselines for both greedy and promoted layouts;
- decide package/repository licensing before external distribution; and
- update the package version, changelog, and release metadata together.

The result should be described as a **template planner**, not an automatic
compliance or construction-design system.

## v0.3: Vehicle and enforced site constraints

Add engineering validity that the current envelope proxies cannot provide:

- a parameterized low-speed vehicle/bicycle model or audited swept-path template
  library;
- minimum-turn-radius, reverse-distance, centerline-crossing, and aisle-end
  checks using the selected vehicle class;
- hard enforcement for obstacles, pedestrian/fire reservations, access routes,
  accessible/EV quotas, and other requirements already represented by the input
  vocabulary;
- explicit distinction between advisory, project-policy, and jurisdictional
  rules; and
- a real-site regression corpus with human CAD comparisons and documented
  tolerances.

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
