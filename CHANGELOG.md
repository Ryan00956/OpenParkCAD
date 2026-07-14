# Changelog

All notable changes to OpenParkCAD are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and package versions
follow semantic versioning while the input/report contracts carry their own
version identifiers.

## [0.3.0] - Unreleased

The implementation is complete on the development branch and remains marked
unreleased until it is merged and tagged. Runtime reports, not the package
version alone, show whether a requested rule actually ran.

### Added

- MIT licensing and package metadata.
- A machine-readable input schema and synthetic, representative
  real-site-shaped regression fixtures.
- A release contract separating advisory, project-policy, jurisdictional,
  proxy, and unsupported rule results.
- Audited turning-radius reference conversion, deterministic low-speed bicycle
  kinematics, and a supported perpendicular-90 reverse-in swept-path template.
- Scoped hard site exclusions, required-route definition checks, and explicit
  accessible/EV quota validation.
- A versioned `engineering_validation` decision that binds vehicle, site,
  authority, quota, official-object, and failure evidence to the exported
  layout.

### Changed

- Requested vehicle checks now fail closed when required geometry or a
  supported template is missing; reverse-distance-only policies expose their
  turning-radius prerequisite.
- Candidate previews and promoted official layouts are revalidated through the
  same vehicle/site gates.
- DXF XDATA, SVG data attributes, and JSON preserve canonical stall-type
  identity.

### Fixed

- Final empty layouts retain the best vehicle rejection evidence instead of
  replacing it with a misleading zero-check pass.
- Hard obstacle, reserved-area, feature, route, and quota failures now block
  transactional output publication.

### Release gate

- Vehicle turn/swept-path and reverse-distance checks must fail closed when
  explicitly requested.
- Active obstacle, reservation, route, feature, and quota constraints must
  validate the exact official layout.
- The report and exporters must preserve rule/object provenance and the
  existing transactional no-output-on-invalid behavior.
- Lint, branch coverage, package build, schema validation, installed-wheel
  smoke tests, and representative fixture invariants must pass.

### Boundary

0.3.0 validates only its documented templates and caller-declared project
policies. It does not certify statutory compliance, accessibility, fire access,
traffic capacity, construction readiness, or physical-world vehicle access.

## [0.2.0] - 2026-07-14

### Added

- Python 3.10/3.12 CI coverage for lint, branch-covered tests, package builds,
  and installed-wheel CLI execution.
- Detailed candidate preview, promotion, operational-quality, and report
  provenance retained through official output generation.

### Changed

- Candidate promotion rebuilds official IDs, revalidates geometry, maneuver,
  graph, and operational decisions, and compares the promoted score.
- Runtime dependencies are limited to geometry/CAD requirements; OR-Tools is an
  optional future optimizer extra.

### Fixed

- Final validity now rejects empty, maneuver-invalid, graph-invalid, or
  operationally hard-rejected layouts before export.
- Declared aisle and entrance relationships require geometric contact before
  becoming traffic-graph edges.
- DXF, SVG, and JSON outputs use transactional group-write/rollback behavior.
- CLI failures return non-zero, SVG text is XML-escaped, and excessive setbacks
  no longer silently disable themselves.

### Boundary

0.2.0 is a trustworthy template-planner closure, not a swept-path, statutory
compliance, traffic-simulation, or construction-design release.

[0.3.0]: https://github.com/Ryan00956/OpenParkCAD/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Ryan00956/OpenParkCAD/releases/tag/v0.2.0
