# OpenParkCAD Input Model Discussion

This document discusses the JSON input model. It is not final API law yet. The
main goal is to separate site facts, design constraints, and optimization choices
so the algorithm can evolve without changing the whole file format every time.

## 1. Design Principle

The input file should describe three different kinds of information:

```text
facts
  Things that are true about the site and cannot be changed by the solver.

constraints
  Things the solver must obey.

choices
  Things the solver may choose while searching for a good layout.
```

Examples:

```text
site boundary shape          -> fact
entrance location and width  -> fact
vehicle turning radius       -> constraint
allow narrow aisles          -> constraint or choice, depending on mode
actual aisle placement       -> choice
stall orientation            -> choice unless user locks it
```

This distinction matters. If a narrow aisle is a site requirement, the solver
must use it. If narrow aisles are only allowed, the solver should compare them
against wide aisles and pick the better design.

## 2. Proposed Top-Level JSON Shape

```json
{
  "version": "0.1",
  "name": "example site",
  "units": "m",
  "standards": {},
  "site": {},
  "site_features": [],
  "entrances": [],
  "pedestrian_and_emergency": {},
  "parking": {},
  "vehicles": {},
  "aisles": {},
  "constraints": {},
  "optimization": {},
  "diagnostics": {},
  "metadata": {}
}
```

Top-level responsibilities:

- `site`: land geometry, obstacles, setbacks, reserved areas.
- `site_features`: fixed site equipment such as columns, curbs, gates, charging
  stations, wheel stops, and signs.
- `entrances`: how vehicles enter and leave the site.
- `pedestrian_and_emergency`: people, accessible routes, fire lanes, emergency
  access, and related no-parking areas.
- `parking`: stall types, quotas, preferred stall families.
- `vehicles`: design vehicle assumptions.
- `aisles`: allowed aisle types and aisle behavior.
- `standards`: jurisdiction and rule profile metadata.
- `constraints`: hard rules.
- `optimization`: soft preferences and scoring weights.
- `diagnostics`: what explanations and debug layers should be exported.
- `metadata`: notes, source, author, coordinate system comments.

## 3. Standards and Rule Profiles

Real parking layouts depend on local rules. The JSON should not pretend one set
of dimensions works everywhere.

The first version should not hard-code legal compliance. It should only record
which standard profile the user intends to apply.

```json
{
  "standards": {
    "jurisdiction": "CN",
    "standard_profile": "custom",
    "strictness": "conservative",
    "notes": "Use project-specific dimensions until local code profiles exist."
  }
}
```

Suggested fields:

```text
jurisdiction
  Country, region, city, or project-specific code context.

standard_profile
  Named rule set. Early values may be custom, conservative, or project_default.

strictness
  relaxed, normal, or conservative. This should influence default margins and
  penalties, not override explicit hard constraints.
```

Important: until the project has verified rule libraries, standards should be
metadata and default guidance, not a claim of legal compliance.

## 4. Site Geometry

The site should support straight edges and curves.

### 4.1 Why Curves Matter

Real land parcels often include:

- arc-shaped curbs,
- rounded corners,
- road frontage curves,
- landscape islands,
- irregular boundary survey lines.

If the input only accepts polygon points, curves must be approximated manually.
That is workable but unpleasant. The JSON should allow curve intent while still
letting the algorithm internally convert curves to polylines.

### 4.2 Proposed Geometry Format

Use a boundary made of segments:

```json
{
  "site": {
    "boundary": {
      "type": "curve_loop",
      "segments": [
        {"type": "line", "from": [0, 0], "to": [60, 0]},
        {"type": "arc", "from": [60, 0], "to": [66, 6], "radius": 6, "clockwise": false},
        {"type": "line", "from": [66, 6], "to": [54, 42]},
        {"type": "line", "from": [54, 42], "to": [0, 36]},
        {"type": "line", "from": [0, 36], "to": [0, 0]}
      ]
    }
  }
}
```

Also allow a simpler polygon form for easy manual input:

```json
{
  "site": {
    "boundary": {
      "type": "polygon",
      "points": [[0, 0], [60, 0], [60, 36], [0, 36]]
    }
  }
}
```

Supported geometry object types should eventually include:

```text
polygon
curve_loop
circle
rectangle
polyline_buffer
```

Phase 0 can document all of these while only implementing `polygon` first.

### 4.3 Curve Tessellation

Algorithms like Shapely work best with polygons. Curves should be converted into
short segments before geometry validation.

Suggested settings:

```json
{
  "constraints": {
    "geometry": {
      "curve_tolerance": 0.05,
      "max_curve_segment_angle_degrees": 5
    }
  }
}
```

Meaning:

- `curve_tolerance`: maximum geometric deviation from the true curve.
- `max_curve_segment_angle_degrees`: maximum angle per generated arc segment.

## 5. Obstacles, Reserved Areas, and Site Features

The site should support multiple area classes:

```json
{
  "site": {
    "obstacles": [
      {
        "id": "building-1",
        "type": "building",
        "geometry": {"type": "polygon", "points": [[20, 10], [30, 10], [30, 18], [20, 18]]},
        "clearance": 0.5
      }
    ],
    "reserved_areas": [
      {
        "id": "landscape-1",
        "type": "landscape",
        "geometry": {"type": "circle", "center": [42, 22], "radius": 3}
      }
    ]
  }
}
```

Initial behavior:

- obstacles are hard no-go areas,
- reserved areas are also no-go unless their type is explicitly allowed later,
- clearance expands the no-go area.

### 5.1 Site Features

Some fixed objects are not full obstacles, but they affect clearance, visibility,
or maneuvering. These should be represented separately from generic obstacles.

Examples:

```text
columns
charging stations
wheel stops
curbs
guardrails
gates
barriers
sign posts
lighting poles
height limit bars
toll or payment equipment
```

Proposed shape:

```json
{
  "site_features": [
    {
      "id": "column-a12",
      "type": "column",
      "geometry": {"type": "circle", "center": [18, 12], "radius": 0.35},
      "clearance": 0.3,
      "affects": ["vehicle_clearance", "door_clearance"]
    },
    {
      "id": "gate-arm-1",
      "type": "gate_arm",
      "geometry": {"type": "rectangle", "origin": [6, 0], "width": 4.5, "height": 0.3, "rotation_degrees": 0},
      "affects": ["entrance_throat", "queueing"]
    }
  ]
}
```

Why keep this separate:

- not every fixed object fully blocks all movement,
- some objects only affect one side of a stall,
- some objects create clearance buffers instead of no-go polygons,
- diagnostics can explain that a stall failed because of a column, post, or curb.

## 6. Entrances and Exits

An entrance should not be just a point. It should describe a boundary opening or
driveway throat.

Proposed shape:

```json
{
  "entrances": [
    {
      "id": "main-gate",
      "mode": "shared",
      "center": [8, 0],
      "width": 7.0,
      "side_hint": "south",
      "heading_degrees": 90,
      "allowed_movements": ["enter", "exit"]
    }
  ]
}
```

Entrance modes:

```text
shared
  The same opening can be used to enter and exit.

entry_only
  Vehicles may enter but not exit.

exit_only
  Vehicles may exit but not enter.
```

Alternative for exact boundary-based input:

```json
{
  "id": "east-exit",
  "mode": "exit_only",
  "edge": [[60, 12], [60, 20]],
  "width": 8.0,
  "heading_degrees": 180
}
```

Important questions:

- Should an entrance be allowed anywhere on the boundary, or must it snap to the
  nearest boundary segment?
- If a user gives `center`, should the solver infer the boundary cut line?
- Should a shared entrance require two-way capacity, or can it be a controlled
  single-lane gate?

Suggested Phase 0 rule:

- accept `center`, `width`, `heading_degrees`, and `mode`,
- draw it in preview,
- do not yet cut the boundary geometry automatically.

## 7. Pedestrian and Emergency Requirements

Parking lots include people and emergency access. These should not be mixed into
ordinary vehicle aisles.

Proposed shape:

```json
{
  "pedestrian_and_emergency": {
    "pedestrian_routes": [
      {
        "id": "walkway-main",
        "geometry": {"type": "polyline_buffer", "points": [[0, 18], [60, 18]], "width": 1.8},
        "priority": "hard"
      }
    ],
    "accessible_routes": [
      {
        "id": "accessible-path-1",
        "connects": ["accessible-stalls", "building-entry"],
        "min_width": 1.5,
        "max_slope": 0.083
      }
    ],
    "fire_lanes": [
      {
        "id": "fire-lane-1",
        "geometry": {"type": "polyline_buffer", "points": [[4, 0], [4, 36]], "width": 4.0},
        "min_turning_radius": 9.0,
        "parking_allowed": false
      }
    ],
    "emergency_access_required": false
  }
}
```

Suggested first behavior:

- pedestrian and fire lane geometries are reserved no-parking areas,
- they may be drawn in diagnostics,
- full pedestrian conflict scoring and fire apparatus swept paths are future
  work.

This module is important because fire access, accessible routes, and pedestrian
safety often override stall-count optimization.

## 8. Parking Stall Information

Parking settings should describe allowed stall families, dimensions, and whether
they are fixed or optional.

```json
{
  "parking": {
    "stall_types": [
      {
        "id": "standard-90",
        "family": "perpendicular",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [90],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": true
      },
      {
        "id": "standard-angled",
        "family": "angled",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [45, 60],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": false
      },
      {
        "id": "parallel",
        "family": "parallel",
        "width": 2.2,
        "length": 6.0,
        "drive_over": false,
        "access_sides": ["left", "right"],
        "enabled": false
      },
      {
        "id": "t-end",
        "family": "t_end",
        "width": 2.5,
        "length": 5.3,
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": false
      },
      {
        "id": "painted-flex",
        "family": "painted",
        "width": 2.5,
        "length": 5.3,
        "drive_over": true,
        "access_sides": ["front", "back", "left", "right"],
        "enabled": false
      },
      {
        "id": "ev-front-post",
        "family": "perpendicular",
        "width": 2.6,
        "length": 5.5,
        "drive_over": false,
        "access_sides": ["front"],
        "blocked_sides": ["back"],
        "fixed_features": [
          {
            "type": "charging_post",
            "side": "back",
            "offset": 0.5,
            "clearance": 0.4
          }
        ],
        "enabled": false
      }
    ],
    "quotas": {
      "accessible_min": 0,
      "ev_min": 0
    }
  }
}
```

Important distinction:

- `family` controls the maneuver model.
- `allowed_angles` controls generation choices.
- `enabled` tells the solver whether it may use that type.
- `drive_over` says whether other vehicles may pass across the marked stall
  surface when it is empty. Most normal stalls should be `false`; some painted
  flexible areas may be `true`.
- `access_sides` says which sides may be used to enter or leave the stall.
- `blocked_sides` says which sides cannot be used because of walls, charging
  posts, curbs, columns, or other fixed objects.
- `fixed_features` describes stall-local objects that affect access and
  clearance.
- quotas impose minimum or maximum requirements.

### 8.1 Stall Access and Pass-Through Behavior

Not all marked parking rectangles behave the same way.

Some spaces are only painted on pavement. When empty, a vehicle may be able to
drive across them in a low-speed maneuver. Other spaces have physical equipment
or obstacles at one side, such as:

- charging posts,
- wheel stops,
- walls,
- columns,
- curbs,
- landscape strips,
- guardrails.

These details change maneuver validity. A stall with a charging post at the back
may only be usable from the aisle-facing front side. A parallel space along a
curb may only be entered from the traffic side. A painted flexible area may be
allowed as temporary pass-through space, but should not be treated like a normal
aisle.

Recommended model:

```text
drive_over
  Whether the stall footprint can be used as temporary drivable surface when
  empty. This is a traffic/maneuver property, not a parking-count property.

access_sides
  Sides from which the design vehicle may enter or leave.

blocked_sides
  Sides blocked by fixed equipment or physical barriers.

fixed_features
  Objects attached to the stall that create clearance or access restrictions.
```

Suggested side names:

```text
front
  The normal aisle-facing side.

back
  The side opposite the aisle.

left / right
  The lateral sides, relative to the stall's local orientation.
```

This distinction matters for optimization:

- a drive-over painted stall may help a maneuver but cannot be assumed available
  if occupied,
- a stall with a blocked side needs a different maneuver envelope,
- a charging stall may need extra clearance around the post,
- a one-sided stall should not be placed where the only usable approach is on
  the blocked side.

## 9. Vehicle Information

Vehicle rules should be explicit. A stall is not valid if the design vehicle
cannot realistically reach and use it.

```json
{
  "vehicles": {
    "design_vehicle": {
      "id": "passenger-car",
      "length": 4.8,
      "width": 1.9,
      "wheelbase": 2.8,
      "min_turning_radius": 5.5,
      "swept_path_margin": 0.3,
      "max_reverse_distance": 12.0
    }
  }
}
```

Phase 0 only needs to represent this. Later phases will use it for maneuver
validation.

## 10. Aisle Information

This is the most important modeling question: should narrow and wide aisles be
given by the user, or should the solver choose them?

The answer should be: both, depending on the field.

### 10.1 Aisle Classes

The input should define aisle classes:

```json
{
  "aisles": {
    "classes": [
      {
        "id": "narrow-one-way",
        "width": 3.5,
        "capacity": "single_vehicle",
        "directionality": "one_way",
        "centerline_crossing": "not_applicable",
        "enabled": false
      },
      {
        "id": "wide-two-way-no-cross",
        "width": 6.0,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "forbidden",
        "enabled": true
      },
      {
        "id": "wide-two-way-cross",
        "width": 6.5,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "allowed",
        "enabled": true
      }
    ],
    "selection_mode": "optimize"
  }
}
```

Suggested fields:

```text
width
  Physical aisle width.

capacity
  single_vehicle or two_vehicle.

directionality
  one_way or two_way.

centerline_crossing
  allowed, forbidden, restricted, or not_applicable.

enabled
  Whether the solver may use this aisle class.

selection_mode
  How aisle classes are chosen.
```

### 10.2 Aisle Selection Modes

There are three useful modes.

#### fixed

The user says exactly which aisle class to use.

```json
{
  "aisles": {
    "selection_mode": "fixed",
    "fixed_class": "wide-two-way-no-cross"
  }
}
```

Use this when:

- the project has a strict standard,
- the user wants predictable output,
- early algorithm phases need reduced complexity.

#### allowed

The user gives a set of allowed classes. The solver may use any of them, but it
does not necessarily optimize between them deeply.

```json
{
  "aisles": {
    "selection_mode": "allowed",
    "allowed_classes": ["wide-two-way-no-cross", "wide-two-way-cross"]
  }
}
```

Use this when:

- the user wants to ban unsafe types,
- the solver can try a small set of variants.

#### optimize

The solver treats aisle class as a design variable.

```json
{
  "aisles": {
    "selection_mode": "optimize"
  }
}
```

Use this when:

- the solver is mature enough to compare tradeoffs,
- the scoring model includes safety and operational penalties,
- narrow aisles and wide aisles may be mixed.

Phase 0 should support documenting all three modes. Phase 1 should probably only
implement `fixed` or a restricted `allowed` mode for wide two-way aisles.

### 10.3 Narrow vs Wide Aisle Recommendation

Do not let early versions freely optimize narrow two-way aisles.

Reason:

- a narrow two-way aisle can maximize stall count while creating head-to-head
  deadlocks,
- a solver that only counts geometry will overuse it,
- narrow two-way aisles require passing-bay, visibility, priority, or short-run
  rules.

Recommended first policy:

```text
Phase 0
  Represent narrow and wide aisle classes.

Phase 1
  Only generate wide two-way aisles.

Phase 2
  Add one-way narrow aisles as directed graph edges.

Phase 5
  Consider narrow two-way aisles only with deadlock and passing-bay checks.
```

## 11. Constraints

Constraints should include hard rules that invalidate a layout.

```json
{
  "constraints": {
    "setbacks": {
      "site_boundary": 0.2,
      "obstacle": 0.3
    },
    "circulation": {
      "require_all_stalls_reachable": true,
      "require_exit_path": true,
      "allow_dead_end_aisles": false,
      "max_dead_end_length": 0,
      "allow_narrow_two_way": false
    },
    "maneuvering": {
      "require_turning_radius_check": true,
      "require_swept_path_check": false,
      "max_reverse_distance": 12.0
    }
  }
}
```

Phase 0 can read or document these, but it should be honest about which ones are
active.

## 12. Optimization Preferences

Optimization settings should not be mixed with hard constraints.

```json
{
  "optimization": {
    "objective": "balanced",
    "weights": {
      "stall_count": 100.0,
      "aisle_area": -1.0,
      "dead_end": -30.0,
      "reverse_distance": -5.0,
      "conflict_point": -10.0
    }
  }
}
```

Suggested objectives:

```text
max_stalls
  Prefer stall count, still respecting hard constraints.

balanced
  Balance stall count, circulation quality, and ease of use.

conservative
  Penalize operational risk heavily.
```

## 13. Diagnostics

A useful design tool should explain what it checked and what it skipped.

```json
{
  "diagnostics": {
    "explain_rejections": true,
    "export_debug_layers": true,
    "include_unsupported_fields": true,
    "report_constraint_status": true
  }
}
```

Suggested diagnostic outputs:

```text
accepted stalls
rejected stall candidates with reasons
aisle candidates
entrance connection status
unsupported input fields
active vs inactive constraints
clearance conflicts
maneuver conflicts
```

This matters because early versions will document more fields than they can fully
use. Reports must be honest about which rules are active.

## 14. Minimal Phase 0 Example

```json
{
  "version": "0.1",
  "name": "phase0 minimal site",
  "units": "m",
  "standards": {
    "jurisdiction": "CN",
    "standard_profile": "custom",
    "strictness": "conservative"
  },
  "site": {
    "boundary": {
      "type": "polygon",
      "points": [[0, 0], [60, 0], [60, 36], [0, 36]]
    },
    "obstacles": []
  },
  "entrances": [
    {
      "id": "main",
      "mode": "shared",
      "center": [8, 0],
      "width": 7.0,
      "heading_degrees": 90,
      "allowed_movements": ["enter", "exit"]
    }
  ],
  "site_features": [],
  "pedestrian_and_emergency": {
    "pedestrian_routes": [],
    "fire_lanes": [],
    "emergency_access_required": false
  },
  "vehicles": {
    "design_vehicle": {
      "id": "passenger-car",
      "length": 4.8,
      "width": 1.9,
      "min_turning_radius": 5.5,
      "max_reverse_distance": 12.0
    }
  },
  "parking": {
    "stall_types": [
      {
        "id": "standard-90",
        "family": "perpendicular",
        "width": 2.5,
        "length": 5.3,
        "allowed_angles": [90],
        "drive_over": false,
        "access_sides": ["front"],
        "enabled": true
      }
    ]
  },
  "aisles": {
    "selection_mode": "fixed",
    "fixed_class": "wide-two-way-no-cross",
    "classes": [
      {
        "id": "wide-two-way-no-cross",
        "width": 6.0,
        "capacity": "two_vehicle",
        "directionality": "two_way",
        "centerline_crossing": "forbidden",
        "enabled": true
      }
    ]
  },
  "constraints": {
    "circulation": {
      "require_all_stalls_reachable": true,
      "require_exit_path": true,
      "allow_dead_end_aisles": false,
      "allow_narrow_two_way": false
    }
  },
  "optimization": {
    "objective": "conservative"
  },
  "diagnostics": {
    "explain_rejections": true,
    "export_debug_layers": true,
    "report_constraint_status": true
  }
}
```

## 15. Phase Support Matrix

The JSON should be future-proof, but implementation should be incremental.

```text
Field or module                         Phase 1 status
------------------------------------------------------
version / name / units                  active
standards                               metadata only
site.boundary polygon                   active
site.boundary curve_loop / arc          documented, future
site.obstacles polygon                  active
site.reserved_areas                     parsed later
site_features                           drawn, not enforced
entrances center / width / heading      active for straight main aisle
pedestrian routes                       reserved no-parking later
fire lanes                              reserved no-parking later
parking.stall_types standard-90         active
parking angled maneuver rule            active as validator-only proxy
parking angled main-aisle generation    active, main aisle only
parking angled branch/connector generation documented, future
parking parallel / t_end                documented, future
parking maneuver rule dispatch          active for active/future rule reporting
stall drive_over                        documented, future maneuver rule
stall access_sides / blocked_sides      documented, future maneuver rule
vehicles design_vehicle                 parsed first, used later
aisles fixed wide two-way               active first
aisles narrow one-way                   future graph phase
aisles narrow two-way                   disabled until deadlock checks exist
constraints entrance-to-main-aisle      active first
constraints dead-end turnaround         active first
constraints stall-to-aisle association  active first
constraints phase1 aisle connectivity   active as simple parent links
constraints full graph circulation      active for generated Phase 2A graph
constraints maneuvering access envelope active as Phase 3A proxy
constraints turning sweep proxy         active as Phase 3B proxy
constraints maneuver rule support       active for Phase 3C-1 dispatch
constraints exact turning radius        documented, future
optimization heading_deltas_degrees     active for main aisle heading trials
optimization entrance_offsets           active for entrance-width offset trials
optimization branch_start_positions     active for perpendicular branch trials
optimization branch_start_step          active for branch start auto-sampling
optimization branch_sides               active for branch side filtering
optimization max_branches               active for limited multi-branch search
optimization enable_connectors          active for same-side branch connectors
optimization connector_throat_length    active for connector-side stall clearance
optimization maneuver_access_depth      active for Phase 3A access envelope
optimization maneuver_access_coverage_ratio active for Phase 3A access envelope
optimization maneuver_turn_buffer_length active for Phase 3B turn proxy
optimization maneuver_turn_coverage_ratio active for Phase 3B turn proxy
optimization maneuver_angled_access_depth active for angled validator proxy
optimization maneuver_angled_turn_buffer_length active for angled validator proxy
optimization maneuver_angled_access_coverage_ratio active for angled validator proxy
optimization maneuver_angled_turn_coverage_ratio active for angled validator proxy
optimization weights                    active for Phase 1 scoring
optimization score breakdown            active in JSON report
report aisles / stalls traceability      active in JSON report
report selected_branches                active in JSON report
report selected_connectors              active, includes removed and added stalls
report traffic_graph validation          active in JSON report
report maneuver_validation              active in JSON report
report maneuver_validation rule_counts  active in JSON report
report attempt graph rejection           active in JSON report
diagnostics                             active as reporting structure
```

Recommended rule: if a field is accepted but not enforced, the report must say
so. Silent partial support is dangerous for a design tool.

## 16. Open Questions

These should be decided before implementation goes too far.

1. Should entrance geometry be defined by `center + width + heading`, by boundary
   edge segment, or should both be supported?

2. Should the first implementation reject curve geometry, or tessellate arcs
   immediately?

3. Should `wide-two-way-cross` be considered a different aisle class from
   `wide-two-way-no-cross`, or should crossing be a rule attached to the maneuver
   model?

4. Should the first valid layout require a loop, or is a short dead-end aisle
   acceptable if reverse distance is below a limit?

5. Should stall dimensions be global defaults, or should each stall type carry
   its own dimensions?

6. Should narrow two-way aisles be fully disabled until passing-bay logic exists?

7. Should `drive_over` become a richer enum such as `never`, `empty_only`,
   `maneuver_only`, and `always`, instead of a boolean?

8. Should fire lanes and pedestrian routes be hard no-parking areas in Phase 1,
   or should they stay documented-only until the graph model exists?

## 17. Recommendation for the Next Implementation Step

For the first parser update, support this subset:

```text
standards as metadata
site.boundary.type = polygon
site.obstacles[].geometry.type = polygon
entrances[].center / width / heading_degrees / mode
vehicles.design_vehicle.length / width / min_turning_radius
parking.stall_types[]
aisles.selection_mode = fixed
aisles.fixed_class = wide-two-way-no-cross
constraints.circulation.allow_narrow_two_way = false
diagnostics.report_constraint_status = true
```

Document curve support now, but implement it after the polygon-based parser is
stable. This keeps the format future-proof without delaying Phase 0.
