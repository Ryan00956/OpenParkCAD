# Changelog

All notable changes to OpenParkCAD are documented here.

## 0.2.0 - 2026-07-14

- Make final generation and export fail closed for empty, graph-invalid,
  maneuver-invalid, or operationally hard-rejected layouts.
- Require declared aisle and entrance links to have geometric contact.
- Rebuild and revalidate promoted candidate layouts before official export.
- Commit DXF, SVG, and report outputs transactionally and escape SVG text.
- Add Python 3.10/3.12 CI with lint, branch coverage, package build, and
  installed-wheel smoke tests.
- Clarify the supported template-planner boundary and reduce runtime
  dependencies.
- Adopt the MIT License.
