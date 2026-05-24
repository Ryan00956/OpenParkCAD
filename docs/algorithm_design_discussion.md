# OpenParkCAD Algorithm Design Discussion

This document is a working discussion for the parking layout algorithm. It is
not an implementation spec yet. The goal is to describe the real design problem
clearly enough that later code can be built in small, testable phases.

## 1. Core View

A parking lot is not just a set of rectangles placed inside a polygon. It is a
traffic system:

```text
site boundary
  -> usable land after setbacks and obstacles
  -> entrance/exit connections
  -> drivable aisle network
  -> reachable parking stalls
  -> vehicle maneuvers into and out of stalls
  -> safety and deadlock checks
```

The algorithm should therefore optimize a connected circulation layout, not only
maximize stall count.

The final score should probably combine:

- number of valid stalls,
- circulation safety,
- aisle length and land efficiency,
- expected conflict risk,
- long reverse distance penalty,
- ease of entering and exiting stalls,
- compliance with configurable local design rules.

## 2. Inputs

Minimum geometric inputs:

- site boundary polygon,
- one or more entrances/exits,
- obstacles or forbidden areas,
- optional internal buildings or reserved areas,
- optional required fire lanes, pedestrian paths, charging zones, accessible
  spaces, loading spaces, and landscape islands.

Minimum rule inputs:

- stall dimensions by stall type,
- aisle width by aisle type,
- minimum turning radius by vehicle type,
- maximum allowed reverse distance,
- one-way or two-way aisle rules,
- whether two-way wide aisles allow centerline crossing,
- setback from boundary and obstacles,
- minimum clearance around stall and aisle edges.

Minimum vehicle inputs:

- vehicle length,
- vehicle width,
- wheelbase if available,
- minimum turning radius,
- swept path envelope or conservative approximation,
- reverse maneuver allowance.

## 3. Design Objects

The algorithm should explicitly model these objects.

### 3.1 Entrance / Exit

An entrance is not just a point on the boundary. It should have:

- location,
- width,
- direction or connected road edge,
- allowed movement: enter only, exit only, or both,
- allowed turning movement if known,
- queue or throat length if needed later.

Every usable aisle network must connect to at least one entrance/exit. A layout
with many stalls but no valid entrance connection is invalid.

### 3.2 Aisle

An aisle is a drivable corridor. Important properties:

- centerline geometry,
- polygon footprint,
- width,
- directionality: one-way or two-way,
- capacity class: narrow or wide,
- whether lane crossing is allowed,
- connected nodes at ends and intersections,
- minimum turning radius at turns and junctions.

Suggested first classification:

```text
narrow one-way aisle
  - only one vehicle can pass
  - no head-to-head meeting if used correctly
  - requires direction graph checks

narrow two-way aisle
  - physically only one vehicle can pass
  - risky unless passing bays, short segment length, or visibility/priority
    controls exist
  - should be heavily penalized or disallowed in early versions

wide two-way aisle
  - two vehicles can pass in opposite directions
  - may or may not allow crossing the centerline during stall maneuvering

wide one-way aisle
  - comfortable circulation but may consume land inefficiently
```

### 3.3 Stall

A stall must include more than its rectangle:

- stall footprint,
- stall type,
- orientation,
- associated access aisle,
- required maneuver envelope,
- entry side,
- exit side if different,
- whether the stall surface can be driven over when empty,
- blocked sides caused by equipment or physical barriers,
- fixed features such as charging posts, wheel stops, columns, or curbs,
- clearance buffer,
- usable door/opening buffer if needed later.

Basic stall types:

```text
perpendicular stall
angled stall
parallel stall
T-shaped / dead-end stall module
accessible stall
EV charging stall
loading or service stall
```

Different stall types need different access space. A perpendicular stall, angled
stall, parallel stall, and T-shaped end stall should not share the same maneuver
rule.

Some stall surfaces are only painted pavement, while others contain or border
physical features. The maneuver model must distinguish a painted stall that may
be temporarily crossed from a stall with a charging post, wall, curb, or column
blocking one side. A stall with a blocked side may only be valid if its usable
entry side is connected to an appropriate aisle.

## 4. Hard Validity Checks

These checks decide whether a generated layout is invalid.

### 4.1 Geometric Containment

- Stall footprint must be inside usable land.
- Aisle footprint must be inside usable land.
- Maneuver envelope must be inside usable land unless a controlled overhang is
  explicitly allowed.
- No stall, aisle, or maneuver envelope may overlap obstacles.

### 4.2 Network Connectivity

- Every aisle segment must belong to the connected drivable network, unless it
  is intentionally marked as a service-only island.
- Every stall must connect to an aisle.
- Every stall must have a path from an entrance to its access aisle.
- Every exit-only route must still allow vehicles to leave.

### 4.3 Direction Feasibility

For one-way aisles:

- the directed graph must allow a vehicle to reach every stall,
- the directed graph must allow a vehicle to leave every stall,
- no one-way branch should trap vehicles without an exit path.

For narrow two-way aisles:

- long head-to-head conflict segments should be invalid or strongly penalized,
- if allowed, there should be passing bays or short controlled sections,
- dead-end narrow two-way aisles need special handling.

### 4.4 Maneuver Feasibility

A stall is valid only if the design vehicle can realistically enter and leave.

Early approximation:

- require enough clear aisle width in front of the stall,
- require an entry-side clearance polygon,
- require a conservative turning-radius envelope.

Better later approximation:

- simulate swept path for forward and reverse maneuvers,
- validate turn from aisle into stall,
- validate exit from stall back into aisle,
- account for whether centerline crossing is allowed on a two-way aisle.

### 4.5 No Complete Blockage

The circulation graph should avoid layouts where normal operations can fully
block each other.

Examples:

- a long narrow two-way aisle with no passing area,
- two vehicles facing each other in a single-lane dead end,
- a one-way path that forces illegal reverse movement,
- a stall maneuver that blocks the only entrance throat for too long,
- a dead-end aisle with no turnaround and too many stalls at the end.

The first implementation can treat these as graph rules and penalties. Later
versions can use traffic simulation.

## 5. Soft Scoring Rules

Not every undesirable pattern must be invalid at first. Some should reduce the
score.

Possible penalties:

- long reverse distance,
- too many dead-end stalls,
- narrow two-way aisles,
- excessive aisle area per stall,
- difficult stall entry angle,
- isolated aisle fragments,
- too many conflict points near entrances,
- stall access that requires crossing a two-way centerline when crossing is not
  allowed,
- complex circulation with unclear driver path.

The score can be shaped like:

```text
score =
    stall_count * stall_value
  - aisle_area * land_cost
  - reverse_distance_penalty
  - conflict_penalty
  - dead_end_penalty
  - maneuver_difficulty_penalty
```

## 6. Candidate Generation Strategy

The solver should not draw one final answer directly. It should generate many
candidate design modules, validate them, then choose the best combination.

Candidate modules:

- straight aisle with one-side parking,
- straight aisle with two-side parking,
- angled one-way module,
- perpendicular two-way module,
- parallel parking along boundary,
- T-end module,
- turnaround bulb or hammerhead,
- connector aisle between two main aisles.

Each module should produce:

- aisle polygon,
- aisle centerline and direction,
- stall candidates,
- maneuver envelopes,
- graph edges and nodes,
- estimated local score.

## 7. Optimization Shape

A practical approach is staged optimization.

### Stage A: Generate Possible Aisle Skeletons

Try several circulation skeletons:

- entrance-to-end spine,
- loop circulation,
- one-way loop,
- parallel aisles connected by cross aisles,
- boundary-following aisle,
- dead-end branches with turnaround.

At this stage, score circulation quality before placing every stall.

### Stage B: Attach Parking Modules

For each aisle skeleton:

- attach stall rows where geometry allows,
- generate different stall types,
- compute maneuver envelopes,
- remove invalid stalls.

### Stage C: Select Compatible Objects

Use a solver later, likely OR-Tools CP-SAT, to choose compatible stalls and
modules:

- overlapping footprints conflict,
- incompatible aisle direction conflicts,
- required access aisle must exist,
- special stall quotas can be enforced,
- maximize weighted score.

### Stage D: Validate Full Layout

After selection:

- run graph connectivity,
- run direction reachability,
- run maneuver checks,
- run blockage and reverse-distance checks,
- export diagnostics.

## 8. Suggested Phasing

### Phase 1: Honest Geometry Kernel

Goal: avoid pretending invalid stalls are valid.

Include:

- usable area from boundary minus obstacles and setbacks,
- entrances as explicit objects,
- aisles as explicit polygons,
- stalls attached to aisles,
- every counted stall has an access aisle,
- multi-angle module trials.

Do not yet solve advanced traffic deadlocks.

### Phase 2: Aisle Network Graph

Goal: make entrance connectivity real.

Include:

- graph nodes at entrance, aisle ends, and intersections,
- directed edges for one-way aisles,
- undirected or two-directed edges for two-way aisles,
- reachability from entrance to every stall,
- exit reachability from every stall,
- dead-end detection,
- simple reverse-distance penalty.

### Phase 3: Maneuver Envelopes

Goal: require that a car can actually park.

Include:

- per-stall-type access envelope,
- turning-radius approximation,
- separate rules for perpendicular, angled, parallel, and T-end stalls,
- centerline-crossing permission on wide two-way aisles,
- invalidation when maneuver envelope collides with obstacles or leaves the site.

### Phase 4: Candidate Optimization

Goal: stop relying on row-by-row greedy placement.

Include:

- module generation,
- conflict graph,
- OR-Tools selection,
- weighted objective,
- quotas and preferences.

### Phase 5: Operational Quality

Goal: avoid layouts that are technically valid but unpleasant or unsafe.

Include:

- long reverse movement detection,
- narrow two-way conflict analysis,
- passing bay rules,
- entrance throat conflict penalties,
- turnaround requirements,
- optional simple traffic simulation.

## 9. Important Design Decision

The project should separate these concepts in code:

```text
geometry validity
traffic graph validity
maneuver validity
optimization scoring
CAD/export rendering
```

If these are mixed together, the algorithm will become hard to trust. A layout
should be explainable:

```text
why this stall exists
which aisle serves it
which entrance reaches it
which maneuver envelope proves it is usable
which constraints rejected nearby alternatives
```

That explainability is probably as important as the final stall count.
