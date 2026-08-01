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

The static analysis is done and is summarised below. The live probe is built
and compile-checked against the running game but **has not been run yet** — the
ordered trace it produces is what turns the section below from "strongly
implied by the class layout" into "measured".

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

**It almost certainly cannot work from the client.** The client sends
`requestInteraction`; the authority holds the element state and rolls the loot.
FareverMeter already measured the loot half of this directly: item grants have
no client-side function hook at all — `st.Loadout.gainItem`,
`st.Player.notifyItemLooted`, `ent.Hero.addInventoryDrop` and
`rpcDoPickup` never fire on a real drop→pickup, because they are server code.
The client only receives `st.Loadout.addItem` + hxbit replication. Re-firing a
local interaction re-asks a question the server has already answered.

**And if some path did work, it would be a loot dupe on a live server**, not a
test harness — real items minted into a shared economy that other players play
in. The measurement goal does not change what the mechanism is.

The probe still hooks `ent.Hero.generateLootItem` / `resolveLootItem` /
`makeLootItem` and `ent.Element.dropLootTable`, because **their absence from a
real chest-open trace is itself the evidence** for the paragraph above. If they
never fire, the roll is server-side and the question is settled by measurement
rather than by argument.

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

`ent.interactible.Refresher`, `Gatherable.respawn` and `getRespawnTime` mean
some interactibles legitimately come back, and `ElementScope.Player` means
chests may be per-player — so sample volume is a routing problem, not a
protocol problem.

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
be attributed. Then open a chest, and afterwards try to open the *same* chest
again so the probe can capture what the refusal looks like.

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
