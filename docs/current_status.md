# Current status

OpenParkCAD is an executable `0.1.0` algorithm prototype. Its implementation
history reaches Phase 5Q, but the trustworthy product boundary is narrower: it
generates and evaluates a small family of parking-network templates and exports
them for engineering inspection.

## Capability matrix

| Area | Current status | Boundary |
| --- | --- | --- |
| Input | JSON boundary, obstacles, entrances, stall/aisle definitions, constraints, optimization settings | Some fields are parsed and diagnosed but not enforced |
| Usable geometry | Polygonal site, setbacks, obstacles, diagnostic site/reservation shapes | No survey/DXF import, curves, slopes, drainage, or topology repair workflow |
| Circulation generation | Straight entrance-connected main aisle, end turnarounds, perpendicular branches, limited same-side U connector | No arbitrary graph, general junction synthesis, or multi-entrance coordination |
| Parking generation | Supported 90-degree and angled main/branch stalls; conservative connector-side 90-degree stalls | No parallel, T-end, connector-side angled, or general mixed per-stall module optimization |
| Candidate selection | Scored template attempts, conflict snapshot, dependency-aware heuristic shadow selection | Greedy/heuristic, not globally optimal; OR-Tools is not called |
| Preview promotion | Full candidate preview, validation, score comparison, guarded replacement of official layout | Explicit opt-in; disabled by default |
| Geometry validation | Containment, obstacle/conflict checks, serving-aisle association | Limited to generated polygonal objects and implemented constraints |
| Traffic graph | Geometric contact for declared links, aisle/stall reachability, entrance/exit path, dead-end turnaround checks | Static consistency proxy, not traffic simulation or proof of capacity |
| Maneuver validation | Rectangular front-access/turn envelopes, angled envelope, optional one-sided L fallback | Clearance proxy only; no vehicle model, steering arc, or swept path |
| Operational quality | Phase 5Q junction, entrance, route, directionality, narrow-two-way, passing-bay, meeting, and merge risks | Geometry/graph proxies; no arrival, priority, queue, or conflict simulation |
| Output | Layered DXF, SVG preview/debug overlay, detailed JSON diagnostics | No interactive CAD editing or construction-document workflow |

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

## Reliability closure in the current tree

The current post-`0.1.0` working tree closes the highest-risk software gaps that
were present in the original prototype:

- declared parent/connector/entrance relationships no longer create usable graph
  edges when the corresponding generated geometry is physically disconnected;
- an operational `hard_reject`, invalid graph, invalid maneuver result, or empty
  layout is rejected before any official artifact is published;
- DXF, SVG, and JSON are committed as one output set, with existing artifacts
  preserved if writing fails;
- promoted candidates are rebuilt with official IDs, revalidated, rescored, and
  reflected consistently in snapshots and provenance;
- user-controlled SVG text is XML-escaped; and
- lint, branch coverage, package build, and installed-wheel execution are
  represented in the Python 3.10/3.12 CI baseline.

These changes improve trust inside the documented template/proxy boundary. They
do not expand that boundary into vehicle simulation or standards compliance.

## Release posture

The project is suitable for:

- algorithm experiments and regression fixtures;
- explainable comparison of supported template layouts;
- DXF/SVG review by a knowledgeable human; and
- developing stronger geometry, vehicle, and optimization layers.

It is not suitable for unattended production design, permit/code claims,
construction output, or safety-critical acceptance. Those claims remain blocked
until the vehicle, standards, real-site validation, and product-integration work
in the [roadmap](roadmap.md) is complete.

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
