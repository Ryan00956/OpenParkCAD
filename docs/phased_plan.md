# OpenParkCAD Phased Plan

This document turns the algorithm discussion into a step-by-step project plan.
It is intentionally conservative: each phase should produce something that can
be inspected, tested, and explained before the next layer is added.

## Guiding Rule

Do not optimize parking count before the layout is provably usable.

The project should advance in this order:

```text
describe the site
  -> describe entrances and rules
  -> describe aisles and stalls
  -> validate connectivity
  -> validate vehicle maneuvers
  -> optimize layout quality
```

## Phase 0: Problem Model and Input Format

Phase 0 is not about generating the best layout. It is about making the problem
expressible without ambiguity.

### Goal

Define the first stable vocabulary and JSON shape for:

- site geometry,
- entrances and exits,
- obstacles,
- standards metadata,
- fixed site features,
- pedestrian and emergency reservations,
- vehicle assumptions,
- aisle classes,
- stall classes,
- design rules,
- expected outputs and diagnostics.

### Why This Comes First

The generator cannot be trusted if the input cannot say things like:

- where cars enter,
- whether an aisle is one-way or two-way,
- whether a narrow aisle may be used in both directions,
- what vehicle turning radius must be supported,
- what kind of stall is being counted,
- whether a stall can be crossed when empty,
- whether a stall has blocked sides or fixed equipment,
- which pedestrian or fire areas cannot be parked on,
- which constraints were skipped.

Phase 0 makes these concepts explicit before the algorithm starts using them.

### Phase 0 Deliverables

1. `docs/phased_plan.md`
   - Defines project phases and acceptance criteria.

2. `docs/input_model.md`
   - Defines the proposed JSON schema in plain language.
   - Shows one minimal example and one richer example.
   - Marks fields as required, optional, or future.
   - Discusses whether aisle width/classes are fixed user inputs or solver
     choices.
   - Separates Phase 1 active fields from future-facing fields.

3. `examples/phase0_site.json`
   - A sample site that includes a boundary, obstacle, entrance, vehicle, and
     basic rules.

4. Parser-level model update
   - The Python data model can read the Phase 0 JSON.
   - The program does not need to use every field for layout generation yet.

5. Diagnostic preview
   - The preview/export can draw the boundary, obstacles, entrances, fixed site
     features, pedestrian routes, and fire lanes.
   - It may still use the current simple layout generator.

### Phase 0 Acceptance Criteria

Phase 0 is complete when:

- a site JSON can represent at least one entrance,
- standards metadata is represented without claiming legal compliance,
- fixed features can be represented separately from obstacles,
- pedestrian/fire reservations have a place in the model,
- vehicle dimensions and turning radius are represented,
- aisle type names are represented,
- stall type names are represented,
- stall pass-through and blocked-side concepts are represented,
- current unsupported fields are preserved or rejected with a clear error,
- diagnostic layers show parsed geometry even when it is not enforced yet,
- the report distinguishes `active`, `drawn_not_enforced`,
  `parsed_not_enforced`, and `future` fields,
- documentation says which fields are active and which are future-facing,
- a user can look at the JSON and understand the design problem being asked.

### Phase 0 Non-Goals

Do not implement these in Phase 0:

- real aisle network graph solving,
- real turning-radius swept path simulation,
- narrow two-way deadlock detection,
- OR-Tools optimization,
- automatic T-shaped stall generation,
- full traffic simulation,
- local legal compliance checking.

These are important, but Phase 0 only prepares the vocabulary.

## Phase 1: Conservative Layout Kernel

### Goal

Generate only layouts that are simple and conservative enough to explain.

Initial allowed design:

- one or more entrances exist in the input,
- wide two-way aisles only,
- standard 90-degree stalls only,
- every counted stall has an adjacent clear aisle strip,
- all generated geometry stays inside usable land.

### Acceptance Criteria

- every stall has an associated aisle,
- the first generated aisle starts from an entry-capable entrance,
- every aisle footprint is inside the usable area,
- every stall footprint is inside the usable area,
- obstacles are respected,
- the report explains the selected angle and stall count,
- unsupported aisle/stall types are ignored or reported clearly.

### Current Implementation Status

Phase 1 is now implemented as a conservative kernel, not as a global optimizer.
The generator records enough metadata for the report to explain:

- which entrance feeds the main aisle,
- which aisle each generated stall uses,
- whether an aisle is the main aisle, a branch, or a turnaround pad,
- which parent aisle a branch or turnaround attaches to,
- which Phase 1 input choices are unsupported and why.

The current report includes `aisles`, `stalls`, `unsupported_phase1_inputs`,
`input_diagnostics.stall_access`, and `input_diagnostics.aisle_connectivity`.

This closes the Phase 1 acceptance criteria for the intentionally narrow
generation pattern below. Full graph reachability remains Phase 2 work.

### First Implementation Slice

The first Phase 1 implementation is intentionally narrow:

```text
entrance -> one straight wide two-way main aisle -> end turnaround -> stalls on both sides
```

Rules:

- use an entrance whose mode allows entering,
- require the entrance width to fit the selected wide two-way aisle,
- try a small set of headings around the entrance heading,
- try a small set of parallel offsets inside the entrance width,
- extend one straight main aisle along the selected heading,
- stop the aisle before it leaves the usable area or hits an obstacle,
- reserve a simple turnaround pad at the far end,
- try one perpendicular branch from the main aisle,
- auto-sample branch start positions unless explicit positions are provided,
- require the branch to reserve its own turnaround,
- leave one aisle-width throat before placing the first stall,
- keep stalls out of the entrance throat and turnaround zone,
- attach standard stalls only to the main aisle,
- report `generation_mode = phase1_main_aisle`.
- report the selected heading and heading delta.
- report the selected entrance offset.
- report branch candidate acceptance or rejection reasons.
- select among candidates using an explainable score breakdown rather than raw
  stall count only.

This proves entrance-to-aisle connection without pretending that a full aisle
graph, loops, intersections, or turning maneuvers exist yet.

### Non-Goals

- do not allow narrow two-way aisles,
- do not allow long dead-end branches,
- do not support mixed stall types yet,
- do not claim global optimality.

## Phase 2: Aisle Network Graph

### Goal

Turn aisles into a graph that can be checked.

Core graph concepts:

- entrance node,
- aisle endpoint node,
- intersection node,
- directed edge for one-way aisles,
- bidirectional edge for two-way aisles,
- stall-to-aisle access edge.

### Acceptance Criteria

- every generated aisle is connected to an entrance,
- every stall can be reached from an entrance,
- every stall has a route back to an exit,
- isolated aisle fragments are invalid,
- dead-end branches are detected and reported.

### Phase 2A: Graph Validation From Existing Layouts

The first Phase 2 slice does not generate new road networks. It converts the
current Phase 1 result into a traffic graph and validates it.

Implemented graph objects:

- `TrafficNode` for entrances and generated aisle pieces,
- `TrafficEdge` for entrance-to-aisle and parent-aisle connections,
- `StallAccess` for stall-to-aisle access,
- `TrafficGraph` as the container,
- `traffic_graph_report()` for JSON report output.

Implemented checks:

- generated aisles are reachable from an entry-capable entrance,
- generated stalls reference existing aisles,
- generated stalls are reachable from an entry-capable entrance,
- generated stalls have a path back to an exit-capable entrance,
- isolated aisle fragments are reported invalid,
- dead ends with turnaround pads are reported as allowed.

The JSON report includes `traffic_graph.nodes`, `traffic_graph.edges`,
`traffic_graph.stall_access`, and `traffic_graph.validation`.

### Phase 2B: Use The Graph To Reject Candidates

The generator now calls the graph validator before accepting a candidate into
the score comparison:

```text
candidate layout
  -> build traffic graph
  -> validate reachability and exit path
  -> reject invalid candidate
  -> score valid candidate
```

Attempt diagnostics include `graph_valid` and `graph_errors`, so an invalid
candidate is rejected with an explanation rather than disappearing silently.

This creates the foundation for multiple branches, loops, intersections, and
eventually one-way aisle networks.

### Phase 2C: Limited Multi-Branch Candidates

The generator can now add more than one perpendicular branch while still using
the Phase 2 graph as a hard validity gate.

Current rules:

- `optimization.max_branches` limits how many branches may be selected.
- The default maximum is `2`.
- Branch start positions still come from `optimization.branch_start_positions`
  or automatic sampling with `optimization.branch_start_step`.
- Each added branch receives a stable id such as `A-BRANCH-001`.
- Every branch has its own turnaround pad.
- A new branch may overlap the main aisle at its junction.
- A new branch is rejected if it overlaps an existing branch, turnaround, or
  branch-served stall.
- Every added branch must pass traffic graph validation.
- Every added branch must improve the score before it is kept.

The report preserves the old single-branch summary fields for compatibility and
adds `selected_branches` for the full selected branch list. Candidate diagnostics
may include:

- `branch_overlaps_existing_layout`,
- `branch_invalid_traffic_graph`,
- `branch_does_not_improve_score`,
- `branch_improves_stall_count`.

### Phase 2D: Same-Side Branch Connectors

The generator can now try a limited connector aisle between adjacent same-side
branches. This creates the first simple loop-like circulation pattern while
staying within the conservative wide-two-way-only model.

Current rules:

- `optimization.enable_connectors` enables connector trials. It defaults to
  `true`.
- `optimization.branch_sides` can restrict branch generation to `left`,
  `right`, or both.
- A connector is tried only between adjacent selected branches on the same side
  of the main aisle.
- The connector is placed near the branch ends.
- The connector may overlap its two endpoint branches and their turnaround pads.
- The connector is rejected if it overlaps other generated aisles.
- The connector removes the two endpoint branch turnaround pads because those
  branches are no longer dead ends.
- Stalls overlapped by the connector are removed from the candidate.
- The candidate must pass traffic graph validation.
- The candidate must improve the score before it is kept.

The report adds `selected_connectors`. Connector candidate diagnostics may
include:

- `connector_geometry_not_possible`,
- `connector_geometry_outside_usable_area`,
- `connector_overlaps_existing_layout`,
- `connector_invalid_traffic_graph`,
- `connector_does_not_improve_score`,
- `connector_improves_score`.

### Phase 2E: Connector-Side Stall Generation

Accepted connector aisles can now serve their own conservative 90-degree stall
candidates. This is still not a full module optimizer, but it prevents a
U-shaped connector from becoming pure circulation space when its sides can
safely hold parking.

Current rules:

- Connector-side stalls are generated only after the connector aisle itself is
  accepted as a geometric candidate.
- `optimization.connector_throat_length` controls the clear space kept near
  each connector-to-branch junction. It defaults to one aisle width.
- The generator tries both connector sides, marked as `outer` and `inner`.
- A connector-side stall must fit inside the usable site.
- A connector-side stall must avoid existing aisles, existing stalls, and the
  connector aisle itself.
- A connector-side stall is counted only if it is served by the connector aisle.
- The final candidate must still pass traffic graph validation.
- The final candidate must improve the score before it is kept.

The report keeps connector details in `selected_connectors` and now includes
`added_stalls` alongside `removed_stalls` and `removed_turnarounds`.

## Phase 3: Maneuver Validity

### Goal

Require that the design vehicle can actually enter and leave each stall.

Start with conservative approximations:

- access envelope in front of the stall,
- minimum aisle width by stall type,
- minimum turn radius clearance,
- no obstacle intersection with maneuver envelope,
- maximum reverse distance rule.

### Acceptance Criteria

- perpendicular stalls use one maneuver rule,
- angled stalls use a different maneuver rule,
- parallel stalls are modeled but may remain disabled,
- T-end stalls are modeled but may remain disabled,
- invalid stalls are rejected with a reason.

### Phase 3A: Conservative Stall Access Envelope

The first maneuver slice is implemented as a conservative geometric access
check, not as full swept-path simulation.

Current rules:

- Each stall must reference a serving aisle.
- The validator finds the stall edge that faces its serving aisle.
- A rectangular access envelope is projected from that edge into the serving
  aisle.
- `optimization.maneuver_access_depth` controls the envelope depth. It defaults
  to one aisle width.
- `optimization.maneuver_access_coverage_ratio` controls how much of the
  envelope must be covered. It defaults to `0.95`.
- The envelope must be covered by generated drivable aisle geometry.
- The envelope must stay inside usable site area, so boundaries and obstacles
  can invalidate it.
- Invalid stalls are filtered before traffic graph validation and scoring.

The report includes `maneuver_validation`, with checked stall count, invalid
stall reasons, filtered stall ids, and access envelope coverage ratios.

Phase 3A deliberately does not check steering arcs, swept paths, or turning
radius. Those remain the next maneuver layer.

### Phase 3B: Conservative Turning Sweep Proxy

Phase 3B adds a first approximation of low-speed turning clearance without
claiming to simulate a vehicle path.

Current rules:

- The Phase 3A access envelope remains the hard front-clearance check.
- A second envelope expands the stall-front access rectangle along the aisle
  direction on both sides of the stall.
- `optimization.maneuver_turn_buffer_length` controls this side expansion. By
  default it uses a conservative value based on stall width, aisle width, and
  vehicle swept-path margin when vehicle data is available.
- `optimization.maneuver_turn_coverage_ratio` controls how much of the expanded
  envelope must be covered. It defaults to the access-envelope coverage ratio.
- The expanded envelope must be covered by drivable aisle geometry.
- The expanded envelope must stay inside usable site area, so side obstacles,
  end walls, and boundary clips can invalidate a stall even when its immediate
  front access is clear.
- Invalid stalls are filtered before traffic graph validation and scoring.

The report includes `turn_buffer_length`,
`minimum_turn_coverage_ratio`, and per-stall turn coverage ratios in
`maneuver_validation`.

Phase 3B is still a proxy. Full steering arcs, swept paths, and exact minimum
turning radius checks remain future work.

## Phase 4: Candidate Generation and Optimization

### Goal

Stop relying on a greedy row scan. Generate candidates first, then choose the
best compatible set.

Candidate objects:

- aisle skeletons,
- stall modules,
- connector aisles,
- turnaround modules,
- passing bay modules.

Optimization should consider:

- stall count,
- aisle area,
- circulation quality,
- conflict points,
- reverse movement,
- maneuver difficulty.

### Acceptance Criteria

- conflicts are represented explicitly,
- OR-Tools or another solver selects compatible objects,
- the objective is documented,
- the report shows why the selected layout won.

## Phase 5: Operational Quality

### Goal

Handle the uncomfortable real-world cases that are technically possible but bad
for drivers.

Checks to add:

- narrow two-way head-to-head conflicts,
- passing bay requirements,
- long reverse movement,
- entrance throat blockage,
- one-way trap routes,
- excessive conflict points near intersections,
- optional traffic simulation.

### Acceptance Criteria

- risky but allowed patterns receive penalties,
- disallowed patterns are rejected,
- reports distinguish invalid constraints from soft penalties.

## Implementation Principle

Keep these layers separate in code:

```text
input model
geometry model
traffic graph model
maneuver model
candidate generation
optimization
export and diagnostics
```

The project should always be able to answer:

```text
Why is this stall valid?
Which aisle serves it?
Which entrance reaches it?
Which vehicle rule does it satisfy?
Which rejected alternatives failed and why?
```

If the program cannot explain that, it should not claim the layout is optimal.
