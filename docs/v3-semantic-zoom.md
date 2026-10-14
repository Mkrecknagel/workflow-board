# Workflow Board v3: semantic zoom into nested boards

Zoom far enough into a component and its own board loads; boards nest like a map. Proof is `tests/test_zoom.py` (`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_zoom.py`).

## Levels and thresholds

Level is derived from `view.k` (canvas clamp 0.02 to 2.5). Every change waits for 250 ms with no wheel or pinch events.

| Transition | Threshold | Hysteresis |
|---|---|---|
| Level 2 to 1 (collapse to silhouettes) | k < 0.40 | expands again only above 0.55 |
| Level 1 to 2 (expand) | k > 0.55 | collapses again only below 0.40 |
| Level 2 to drill | k >= 1.6, a `drill` card fills >= 60 % of the viewport width or height, nearest centre wins, 250 ms settle | child opens at fit view |
| Child to parent | k < 0.25 with a breadcrumb parent, 250 ms settle | parent reopens with the drilled card centred at k = 1.0 |

- Level 1 collapses each `group` (workflow boards) or `boundary` (architecture boards) to a silhouette with a count; loose cards become chips.
- Level 3 is the existing workflow board. There is no Code level.
- Alternatives to the zoom rule: double-click a drillable card, select it and press Enter, or click its `Enter ▸` badge. Esc goes up one level.
- Touch (`pointer: coarse`): never auto-loads and never auto-returns. Drillable cards show `Open ▸`; the breadcrumb is the way back.
- Navigation: breadcrumb HUD top-left (depth capped at 4); hash route `#b=<board>&k=&x=&y=`; `pushState` on drill and return, `replaceState` on view settle (500 ms); back and forward work.
- A drill target already in the breadcrumb renders as `↑ up to X`. A missing target renders a red `missing board` pill and drilling is disabled.

## Card types (B3)

| Type | `ref` required | Ports | Shape |
|---|---|---|---|
| `boundary` | `kind`: machine, vm, network, trust, entity, cloud | none | dashed box sized by `size {w,h}`, drawn behind other cards; only its header is draggable |
| `component` | `kind`: daemon, service, model, schedule | in `uses`, out `provides` | rounded box |
| `datastore` | `kind`: file, db, git, cloud, plus `path` | in `write`, out `read` | cylinder-style rounded bottom |
| `external` | `vendor`, `protocol` | in `call`, out `event` | dashed pill |
| `person` | `role` | none | rounded top |

These types are structural: they have no telemetry and are never greyed. Every card carries `data-type`.

Top-level card keys: `drill` (board id), `boundary` (id of the containing boundary card), `size {w,h}`.
`fields` values are scalars; lists are comma-separated strings. Fields in use:

| Field | Meaning |
|---|---|
| `owner` | M, K, shared, vendor |
| `cost` | zero, metered, fixed |
| `authority` | datastore only: SoT, derived (shown as `cache`), snapshot |
| `secrets` | comma-separated secret names; shows a key badge |
| `entity` | Organization or entity the card belongs to |
| `redundant_with` | id of the card that covers it; clears SPOF |
| `spof` | `"true"` forces SPOF |
| `depends_on` | boot order, recorded only, not computed |
| `verified` | `false` marks an unconfirmed fact |
| `teach` | what it is and why it sits here; shown in the inspector and on hover (library item or card) |

Containment is explicit. Dropping a card inside a boundary box sets `boundary`; dragging it out clears it; the smallest box containing the card centre wins. Overlap without the field is a `validate.py` warning, never silent inheritance. Moving a boundary does not move its members.

## Summary pill

A card with `drill` shows a pill read from the child file when the board loads, for example `127 cards · 16 hooks · 0 gates · observe · saved 10-05`. It is cached per session, never typed. If the child cannot be read it shows `summary unavailable`.

## Badges and lenses

| Badge | Source |
|---|---|
| `SPOF` | `fields.spof == "true"`, or a boundary holding 3 or more component or datastore cards none of which has `redundant_with` |
| `unverified` | `fields.verified == false` (dashed amber) |
| `SoT`, `cache`, `snapshot` | datastore `fields.authority` |
| `key` | `fields.secrets` |
| `ext` | `external` type |
| `$` | `fields.cost` is metered or fixed |
| `M` / `K` / `shared` / `vendor` | `fields.owner` |

Lenses (one active at a time): SPOF, Trust, Cost, Entity, External, Blast. A lens with nothing to compute says `no data on this board`.

## Modules

A module may contain boundary cards. `boundary`, `drill` and `size` survive Save as module. On save, a `boundary` pointing outside the selection is dropped; on insert, `boundary` is remapped onto the fresh card ids, so nothing dangles.

## Not covered

GPU saturation, boot-order computation, latency hops and lock contention are named in the level panel but not computed. Parent ports are declared contracts; `validate.py` warns when a child's trigger or output set no longer matches.
