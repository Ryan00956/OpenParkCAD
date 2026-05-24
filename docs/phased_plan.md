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
- every aisle footprint is inside the usable area,
- every stall footprint is inside the usable area,
- obstacles are respected,
- the report explains the selected angle and stall count,
- unsupported aisle/stall types are ignored or reported clearly.

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
