# FareverChest

A standalone spike into **how Farever decides a chest has been opened**, and
what that means for measuring drop rates. Separate project, separate directory,
but it reuses the machinery FareverMeter paid for: the HashLink bytecode parser,
the `functions_ptrs` resolver, the name-resolved offset emitter, the staleness
gate, and the game-thread rule.

Everything here is **read-only**. Every Interceptor logs; nothing is called, no
argument is replaced, no game memory is written.

---

## Status

Static analysis done, **and confirmed live** against the running game on
2026-08-01 (`logs/chest-20260801-155552.log`, ~5 min, 8 chests in the layer,
all of them already opened on that character). Results in
"What the probe measured" below. The static section that follows it is what
the bytecode implied; the measured section is what actually happened.

---

## What the probe measured (2026-08-01, live)

### The detection mechanism, caught in the act

Every chest streams in with `currentVisualState = null` and is then handed to
`st.WorldContext.getElementState`. The answer to that lookup is what sets the
visual:

```
Z1_World_Greenlands_WorldChest_41    Closed|null|1  ->  Closed|Opened|1
Recipe_Chest_Root_11                 Closed|null|1  ->  Closed|Opened|1
Z1_World_Greenlands_Camp_2_Chest_1   Locked|null|1  ->  Locked|Opened|1
FightStone                           Closed|null|1  ->  Closed|Closed|1   <- not opened
                                    (stateId|currentVisualState|enabled)
```

That `FightStone` row is the control: same code path, same streaming, and it
resolves to `Closed` because the ledger has no entry for it. **The chest does
not remember anything — the WorldContext does, and the chest asks.**

`stateId` never moved on any of the eight, confirming the meter's earlier
measurement. It is the CDB-authored *design* state (`Closed`, or `Locked` for
a chest that needs a key), not runtime state. `WorldContext.getElementState`
was called 9,218 times in five minutes, 6,303 of them carrying a single nearby
chest — this lookup is polled continuously, not cached onto the entity.

### An opened chest never even asks the server

Standing directly in front of an already-opened chest, **no interact prompt
appears at all**, and the RPC path is never entered:

```
requests that reached ent.Interactible.requestInteraction, by target:
    InstanceOrb(POI_Rift_Entrance_3)   x1        <- a rift portal, worked fine
    Chest(...)                          x0
```

The rift portal is the positive control that proves the hooks work: it went
the whole way through `tryRequestInteraction -> requestInteraction ->
checkRequestInteraction -> apiRpcRequestInteraction -> checkUse`. Chests
reached none of it.

What runs instead is the client-side gate, polled every frame:

```
PlayerController.getClosestInteractible   x11,711
Interactible.getInteractCooldown          x52,104
Interactible.canInteract                   x9,321
Interactible.canActivate                   x8,547
```

For an opened chest those predicates return false, so the prompt is suppressed
and no request is ever sent. The refusal is **client-side and silent**, ahead
of the server ever being asked.

### The client does not roll loot — measured, not argued

These hooks fired **zero times** across the entire session, including during
the real rift-portal interaction:

```
ent.Hero.generateLootItem          ent.Hero.generateWorldLootItem
ent.Hero.resolveLootItem           ent.Hero.makeLootItem
ent.Element.dropLootTable          st.Loadout.addItem
st.player.Progress.incrementItemLoot
```

They exist in the binary because client and server share one Haxe codebase.
They do not execute on the client. This is the same result the meter reached
for item pickups, now confirmed on the chest path specifically.

---

## What the bytecode says (measured from `hlboot.dat`, 47,342 functions)

### 1. The Chest class is a replication target, not a game object

`ent.interactible.Chest extends ent.Element` and adds **zero fields of its
own**. Its full method list is:

| method | what it is |
|---|---|
| `onPlayerActivate` | the only gameplay method |
| `getCLID`, `networkFlush`, `networkSync`, `unserialize`, `markReferences`, `getVisibilityMask` | hxbit networking boilerplate |

There is no `open()`, no `loot()`, no `roll()`. Compare `ent.interactible.Gatherable`
in the same package, which *does* carry `doActionServer`, `consume`, `rollAffix`,
`respawn` and `getRespawnTime` — those are server-side methods compiled into the
same binary, because client and server share one Haxe codebase.

### 2. The interaction is an RPC, and the client is the requester

`ent.Interactible` carries the full hxbit request/impl pattern:

```
requestInteraction / requestActivate        <- client sends
  apiRpcRequestInteraction                  <- dispatch
    apiRpcRequestInteraction__impl          <- body, runs on the authority
```

plus `canRequestActivate`, `checkRequestActivate`, `canPlayerActivate` and
`getInteractCooldown` on the client side. The client's job is to *ask*.

### 3. The "already opened" record is not on the chest

`ent.Element` has `stateId` and `currentVisualState`. FareverMeter already
measured what these do when you loot a chest:

```
before   stateId Closed   currentVisualState Closed
after    stateId Closed   currentVisualState Opened
```

`stateId` does not move. `currentVisualState` is cosmetic — it is what the
model plays, and filtering the minimap on it was correct only because a spent
chest happens to look different.

The real ledger is **`st.WorldContext.elements`**, a `haxe.ds.StringMap`, with
`getElementState` / `setElementState` / `_setElementState__impl` /
`resetElementState`. And it is scoped:

```
ent.ElementScope = World | Group | Player
st.Player.playerContext : st.WorldContext     <- the per-player one
```

So "has this chest been opened" is answered by a keyed lookup in a world
context whose scope decides whether it is per-player, per-group, or global.
The probe hooks those three functions and prints the key and value rather than
guessing at the StringMap layout.

### 4. Drop rates in this game are deliberately **not** independent

This is the finding that matters most for the original goal.
`st.player.HeroData` carries:

```
worldLootLog : hl.types.ArrayObj      (replicated to the client)
lootLogContainsItem(...)              (a predicate that reads it)
```

and the string pool carries `BLP_LootLog`, `LootLog_Size`,
`LootLog_LevelDifference`, alongside the `RegisterLootLog` flag in
`st.LootDropFlags`. **BLP = bad-luck protection.** The game keeps a rolling log
of what you have recently looted from world sources and consults it when
rolling the next drop.

Any drop-rate study that assumes independent rolls will produce numbers that
are wrong in a specific, structured way — and re-sampling the *same* chest as
fast as possible is exactly the sampling scheme most distorted by a
recency-based pity/dedup system.

---

## On re-opening a chest repeatedly

The original ask was to make a chest re-openable back-to-back to sample drops.
Based on the above, that is not something this project will do, for two
separate reasons.

**It cannot work from the client — this is now measured, not inferred.** There
are two independent walls, and the probe saw both:

1. The opened-ness answer comes from `WorldContext.getElementState`, which the
   client *queries*. Forcing the local answer would flip the chest's model back
   to `Closed` and re-show the prompt — a lie told to your own renderer. The
   request would then go to a server that still has the real entry.
2. The client never rolls the loot. `generateLootItem`, `resolveLootItem`,
   `makeLootItem` and `dropLootTable` fired zero times in a session that
   included a real, successful interaction. There is no local roll to re-run,
   so there is nothing client-side that could produce a second sample even in
   principle.

Re-firing a local interaction re-asks a question the server has already
answered, using a roll the client does not own.

**And if some path did work, it would be a loot dupe on a live server**, not a
test harness — real items minted into a shared economy that other players play
in. The measurement goal does not change what the mechanism is.

The probe hooks those loot functions precisely because **their absence from a
real trace is the evidence**, and that is how the question got settled — by
measurement rather than by argument.

### What actually answers the drop-rate question

A **loot logger**: record every chest open and everything that arrived, across
normal play, and accumulate real empirical statistics. It is honest, it needs
no server-side lie, and given the bad-luck-protection finding it is also the
*more accurate* instrument — it samples the distribution the game actually
implements, including the BLP behaviour, instead of hammering one input that
the pity system is specifically designed to respond to.

Better still, because `worldLootLog` is replicated to the client, a logger can
read the BLP ledger alongside each drop and characterise how the log changes
the odds. That is a genuinely more interesting result than a flat drop rate,
and nothing about it requires touching the server.

**Sample volume is a routing problem, not a protocol problem.** Chest state
appears to be per-character — Brudr has every chest opened on the current
character while the world still streams them in as entities — which matches
`ElementScope.Player` and `st.Player.playerContext`, though the scope field
itself has not been read directly yet. A fresh character therefore sees a full
world of unopened chests. `ent.interactible.Refresher`, `Gatherable.respawn`
and `getRespawnTime` mean some interactibles legitimately come back as well.

So the practical instrument is: a logger, a route, and repetition across
characters or respawn cycles — with `worldLootLog` read alongside each drop so
the BLP effect can be separated from the base rate instead of silently
contaminating it.

---

## Layout

```
hltools/
  hlbc_parser.py            HashLink bytecode reader        (from FareverMeter)
  gamepath.py               locate hlboot.dat               (from FareverMeter)
  datafresh.py              refuse to run against stale data(from FareverMeter)
  chest_survey.py           dump every chest/loot class, field and method
  find_openrecord.py        find where "already opened" is stored
  find_lootlog.py           find the BLP loot log + element state machinery
  build_chest_targets.py    emit resolver_data.json + chest_offsets.json
frida/
  chest_probe.js            the read-only probe (46 hooks, ordered trace)
  run_chest.py              host; logs to logs/chest-<stamp>.log
analysis_out/               generated; regenerate after every game patch
```

## Running it

Every findex and offset in `analysis_out/` is a snapshot that **goes stale at
each game patch**. Regenerate first, always:

```bash
py hltools/build_chest_targets.py
```

Then, with the game running:

```bash
py frida/run_chest.py 900
```

Wait for the `PROBE ARMED` line before touching anything in game — the hooks
are live before that, but the local hero is not latched and the trace will not
be attributed.

Note that on a character with everything already looted there is nothing to
press: an opened chest shows **no interact prompt at all**. That is not the
probe failing, it is the result (see the measured section above). To capture a
successful open, run this on a character with unopened chests.

`run_chest.py` aborts if `analysis_out/` was built from a different
`hlboot.dat`. That gate exists because the failure it prevents is silent: stale
native anchors resolve the `functions_ptrs` base to a self-consistently wrong
address, every findex then points at an unrelated function, and the probe
reports nothing while appearing to work.

## The thread rule

Any HL call from a frida timer thread dies with *"Can't lock GC in unregistered
thread"*, non-deterministically, and it has crashed the live game before. The
only HL call in this probe is the `getMyHero` lookup, and it runs inside the
`client.BaseCamera.postUpdate` hook — on the game thread. The 400 ms sweep does
plain pointer and string reads and nothing else.

---

## The Radiance question (2026-08-01)

"Radiance" is the item `Staff_Craft` — a Staff, `faction: World`, base level 4
(item level is rolled at drop time, so a level-25 one is normal). Two other
things in the game are also called Radiance (`Scepter_Start_Skill2` and
`Priest_Talent_Radiance`, both skills); those are not it.

**The chests people point at are `CrimsonCrate` chests.** Measured: opening
`Z1_World_Greenlands_WorldChest_12` fired
`WorldContext._setElementState__impl("Z1_World_Greenlands_WorldChest_12", …,
"Opened")` exactly once. Its prefab
(`Level/World/W1_Siagarta.dat/gameplayData/L0_+6_-7.prefab`) declares:

```
lootTable  CrimsonCrate      faction  Crimson
zoneBaked  Z3_CrimsonIsland_Cathedral
```

The `Greenlands` in the element id is a stale level-design name — these are
physically in Crimson Island Cathedral, which is why the zone never matched.

**Radiance is reachable, and the path is intact.** `CrimsonCrate` contains no
explicit weapon rows at all. The only weapon path is the `WorldLoot` token at
`proba 0.35` inside `WorldCrate`, which the server expands via
`$HItem.isWorldLoot` / `getFactionLootTable` / `generateLootTable` and
`ent.Hero.generateWorldLootItem`. The `faction: World` pool is exactly six
items, and Radiance is one of them:

```
Sword_Craft  Shield_Craft  Daggers_DuplicatePoison
GA_Craft     Bow_Craft     Staff_Craft (Radiance)
```

If that generator draws uniformly, Radiance is roughly `0.35 / 6 ≈ 5.8%` per
chest — but **that is an assumption, not a measurement**: the generator body is
server-side and this parser deliberately does not read opcodes, so the weighting
(and any level/aptitude filter, and the separate `WorldLootWithAffinity` token)
is unverified.

### A real anomaly, though — CrimsonCrate is the only bare faction crate

Every other faction crate layers faction content on top of `WorldCrate`.
`CrimsonCrate` does not:

```
BeeCrate      -> NatureWeights + WorldCrate
KoboldCrate   -> Ores + EarthWeights + WorldCrate
ManfishCrate  -> WaterWeights + WorldCrate
DemonCrate    -> ChaosWeights + WorldCrate + Demon_Weight
CrimsonCrate  -> WorldCrate                      <- nothing else
```

`CrimsonCrate` also never references the `Crimson` faction table, so everything
that table would contribute — `Ramgold`, `StaleBread`, `Pumpkin`, `Cheese_Z2`,
its own extra `WorldLoot` rolls, and `Mount_Hound_01` at `0.001` — cannot come
out of these chests at all. That is a concrete content bug worth reporting, but
it is **not** the Radiance bug: the `WorldCrate → WorldLoot` path that produces
Radiance is present and unaffected.

### On re-opening to test this

Not built, and not buildable client-side. Measured twice: the client never
rolls loot (every loot function stayed at zero across two sessions including a
successful open), and the opened-record RPC *arrives* at the client from the
authority. There is no local roll to re-run. See the section above.
