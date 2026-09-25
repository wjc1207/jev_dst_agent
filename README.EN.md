# JEV DST Agent 1.0.6 — telemetry, bounded controls and JEV decisions

This package connects JEV decisions to a bounded Don't Starve Together client Mod.
It exports game state and can execute only the actions explicitly listed below.

## What it exports

Once per second, the client mod writes one JSON record to `client_log.txt` with:

- day, phase and world time;
- health, hunger and sanity;
- inventory and equipped items;
- whether a torch or campfire can be crafted;
- nearby useful items, resources, light sources and threats.
- persistent frontier-navigation state, target point and current leg.

Short records start with `[JEV_DST_STATE]`. Records longer than the safe game-log
line size are emitted as numbered `[JEV_DST_CHUNK]` lines and reassembled by
Python. A trailing tab added by the game log is removed from each part first.
The controller and watcher accept only complete, valid JSON records;
truncated lines left by older Mod versions are ignored.

On day one, the compact JSON sent through the JEV API's `state` field also
contains a `knowledge` array of plain-English game facts. It explains first-night
darkness damage, the torch recipe (two twigs and two cut grass), where those
materials come from, dusk urgency, torch durability, and the campfire
alternative. The final knowledge line is dynamic: it reports current materials,
missing torch ingredients, torch craftability, and whether first-night light is
ready. This information guides JEV but does not remove choices or force an
action. The `knowledge` field is omitted after day one.

## Install the mod

Copy the entire `jev_dst_agent` folder into DST's `mods` directory. On this PC,
the detected destination is:

```text
C:\Program Files (x86)\Steam\steamapps\common\Don't Starve Together\mods\jev_dst_agent
```

Start DST, open **Mods**, enable **JEV DST Agent**, apply the changes, and enter a
local/private world.

## Watch live state

Open PowerShell in this folder and run:

```powershell
C:\Users\16425\anaconda3\python.exe .\watch_state.py
```

For the complete JSON records:

```powershell
C:\Users\16425\anaconda3\python.exe .\watch_state.py --full
```

## Bounded controls

`controller.py` supports movement, collection, crafting, work, food and combat operations:

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py status
C:\Users\16425\anaconda3\python.exe .\controller.py move up --seconds 0.25
C:\Users\16425\anaconda3\python.exe .\controller.py interact
C:\Users\16425\anaconda3\python.exe .\controller.py collect grass
```

`collect` selects the nearest matching observed prefab, approaches it through
short WASD pulses, waits for fresh telemetry after every pulse, then presses the
ordinary action key. `stop` releases every movement key if a run is interrupted.
JEV's safe loose-resource candidates include flint, logs and rocks.
Collection approaches to within one world unit before pressing Space. Success is
verified when a source loses its `pickable` tag or a loose item disappears; if
the interaction does not complete, the controller retries instead of advancing
the JEV loop.

Grass, saplings and berry bushes expose an explicit live `pickable` value and
are omitted from JEV's candidates while empty. Immediately before Space, the
controller waits for a newer state and checks the same value again. A matching
inventory gain (cut grass, twigs or berries) also proves that collection has
finished, even though the harvested plant entity remains in the world.
The chosen collectible's GUID is carried into execution, so a nearer depleted
plant cannot replace the live target. If that GUID becomes unavailable before
execution, the controller falls back to the nearest currently collectible plant
of the same prefab.

## Offline automatic tests

Run the complete regression suite without launching DST, calling JEV or sending
any keyboard input:

```powershell
C:\Users\16425\anaconda3\python.exe .\run_tests.py
```

The suite uses simulated telemetry and mocked game input. It covers depleted
versus live resource selection, GUID handoff, fallback selection, and collection
completion when inventory increases while the harvested plant remains visible.

Collection movement uses distance-scaled pulses: up to one second while far
away, braking to 0.18 seconds near interaction range. An active pursuer within
five world units interrupts collection immediately. When pursued at close range,
an unarmed character receives only a flee action; a carried weapon can be
equipped, and attack is exposed only while a weapon is already equipped. Fleeing
remains available during night and below 20% hunger. Only entities marked
`activeThreat` expose fleeing. A flee action is a bounded sequence: after every
one-second movement burst the controller reads fresh telemetry and recomputes
the direction away from the pursuer. It stops after two consecutive clear
observations, or after eight bursts as a safety limit.

Light management is phase-driven: the equip action is hidden during day and
dusk, while unequipping remains one option among JEV's other actions. At night,
a torch is equipped only when no lit campfire or firepit is within eight world
units. Campfires can be built only at night; while beside a lit campfire or
firepit, the equip action is hidden and unequipping remains available without
forcing it. Movement away from light while unequipped remains blocked unless
fleeing an immediate hostile.

Torch actions are semantic Mod operations with post-action verification:

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py craft-torch
C:\Users\16425\anaconda3\python.exe .\controller.py equip-torch
C:\Users\16425\anaconda3\python.exe .\controller.py unequip-torch
```

The JEV candidate list contains `craft_torch` only while telemetry reports that
the recipe is craftable and no torch is already held. `equip_torch` appears only
at night while a torch exists, is not equipped, and no lit campfire or firepit
is nearby. `unequip_torch` appears during day or dusk while a torch is equipped,
and at night when a lit campfire or firepit is nearby.

Additional bounded actions:

```powershell
C:\Users\16425\anaconda3\python.exe .\controller.py craft-axe
C:\Users\16425\anaconda3\python.exe .\controller.py craft-pickaxe
C:\Users\16425\anaconda3\python.exe .\controller.py chop
C:\Users\16425\anaconda3\python.exe .\controller.py mine
C:\Users\16425\anaconda3\python.exe .\controller.py eat
C:\Users\16425\anaconda3\python.exe .\controller.py build-campfire
C:\Users\16425\anaconda3\python.exe .\controller.py attack
C:\Users\16425\anaconda3\python.exe .\controller.py equip-weapon
C:\Users\16425\anaconda3\python.exe .\controller.py flee
```

Attack is exposed only when telemetry sees a valid hostile/monster target and a
weapon is already equipped, then sends DST's native `Ctrl+F` command; it
does not depend on mouse coordinates or the Mod's F10 bridge. Campfire construction follows DST's
native two-stage flow: buffer/craft the recipe, then place it. Chop and
mine now continue until the chosen target is completely harvested. Their JEV
actions are exposed only when telemetry proves that a matching basic tool has
enough remaining durability for the full job: normal trees are budgeted
conservatively at 15 chops; common rocks use their game-defined 6/4/2 strikes.
Eating uses a conservative early-game food whitelist.

## Candidate-pruning principle

Candidate pruning does not choose an action on JEV's behalf. Its purpose is to
remove actions that are unavailable, contextually meaningless, or excluded by
an explicit safety constraint. JEV still ranks and selects among all remaining
meaningful actions.

For example, an equipped torch during day or dusk exposes `unequip_torch`
because putting it away preserves fuel, while `equip_torch` is omitted when no
torch is equipped because carrying a lit torch in daylight provides no useful
benefit. At night the relationship reverses when there is no other light source;
beside a lit campfire or firepit, equipping a torch is again omitted as
redundant. Similarly, depleted plants, unavailable recipes, insufficiently
durable tools, and frontier legs rejected by the world map API are not shown as
candidates. The pruning layer determines which operations currently make
sense; it does not decide which sensible operation is best.

## JEV decision

Put the TypeSafe key in `.env` (see `.env.example`). A decision is dry-run by
default:

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --show-probabilities
```

Add `--execute` only after reviewing a dry run:

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --execute
```

For continuous autonomous control, use:

```powershell
C:\Users\16425\anaconda3\python.exe .\jev_agent.py --loop --execute
```

The loop has no cooldown by default and senses again immediately after each
completed action. Use `--interval 1` or another value up to 60 seconds to add a
cooldown. Errors still use a minimum one-second exponential retry delay to avoid
a tight failure loop.
Long actions such as chopping, mining, collecting and building finish before the
next cycle begins, so actions never overlap. Day 2 is not a terminal state: the
loop continues indefinitely and stops only on death or when interrupted with
`Ctrl+C`. Transient errors retry with bounded exponential backoff.

Exploration is a single semantic `explore` action rather than four directional
choices. The Mod divides the world into four-unit cells and remembers cells
observed within the telemetry scan radius. A frontier is a known passable cell
adjacent to at least one unknown cell. The current baseline score is:

```text
score = information_gain * 10 - distance * 0.5 - visits * 6
```

The highest-scoring reachable frontier becomes a persistent target. The target
survives intervening collection, crafting and other decisions until it is
reached or its next path segment becomes invalid. An invalid target is
blacklisted for 30 seconds before another frontier is selected.

One `explore` execution walks only the next four-unit leg toward that target.
The Mod samples that leg every 0.75 units with
`TheWorld.Map:IsPassableAtPoint`; an invalid leg is never exposed to JEV. After
each leg the controller stops, fresh telemetry is emitted, and JEV decides
whether to continue exploring or perform another meaningful action. Direction
keys are only an execution detail derived from the target vector. Adjust the
duration of a full four-unit leg with `--move-seconds`; the default is one
second and accepted values are 0.2–2.0 seconds.

The controller checks actual position after each leg. If the same frontier
produces less than 0.15 units of progress twice in succession, it asks the Mod
to blacklist that frontier for 30 seconds and select another one. JEV still
chooses the next action after each leg. If the player moves during a JEV call,
the controller follows the Mod's latest passable leg for the same frontier.

Equipping a torch uses the same inventory-tile action as manually clicking the
torch, rather than the action-system auto-equip helper.

Safe resource collection uses a `0.25` confidence threshold. The JEV prompt also
explicitly prefers unequipping a torch during day and dusk to preserve its
durability.

Execution uses risk-based confidence thresholds by default: `0.15` for a short
exploration step, `0.25` for collecting an observed safe resource, and `0.00`
for waiting. Crafting a torch and building a campfire each use `0.45`. Pass
`--min-confidence` to override these values globally. Moving without an
equipped torch at night is always blocked by a hard safety rule, except that
fleeing from an immediate hostile remains permitted. Attack uses a `0.45`
threshold and is unavailable while unarmed. `.env` is ignored by Git and
deliberately excluded from release archives.
