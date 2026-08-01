"""Survey everything the bytecode knows about chests.

Prints, for each interactible/loot-ish class: field layout with byte offsets,
and every method with its findex and signature. Also greps the whole function
namespace for open/loot/interact vocabulary so nothing on the path is missed.

    py hltools/chest_survey.py [path\\to\\hlboot.dat]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT          # noqa: E402
from gamepath import find_hlboot                        # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "analysis_out"
OUT.mkdir(parents=True, exist_ok=True)

HLBOOT = find_hlboot()
print(f"[*] parsing {HLBOOT}")
code = HLCode(HLBOOT).parse()
print(f"    version={code.version} debug={code.has_debug} "
      f"functions={code.counts['nfunctions']} types={code.counts['ntypes']}")

names = code.findex_names()
by_name = {t.name: t for t in code.obj_types()}

# ---------------------------------------------------------------- classes ----
# Anything in the interactible package, plus the loot/chest vocabulary anywhere.
CLASS_RX = re.compile(r"(interactible|chest|lootbox|loot|treasure|reward|"
                      r"container|coffer|strongbox)", re.IGNORECASE)
classes = sorted(t.name for t in code.obj_types() if CLASS_RX.search(t.name))
print(f"\n=== classes matching loot/interactible vocabulary ({len(classes)}) ===")
for n in classes:
    t = by_name[n]
    sup = code.types[t.super_index].name if 0 <= t.super_index < len(code.types) else None
    print(f"  {n:<50} extends {sup}")

# ------------------------------------------------------------- deep dump -----
FOCUS = [n for n in classes if re.search(r"(Chest|Element|Interactible)$", n)]
# Always include the base entity classes the chest inherits from.
for extra in ("ent.Element", "ent.GameObject", "ent.Entity"):
    if extra in by_name and extra not in FOCUS:
        FOCUS.append(extra)

dump = {}
for n in FOCUS:
    t = by_name[n]
    offs = code.field_offsets(t.index)
    protos = {}
    # Walk the super chain so inherited methods show up too.
    chain, i, seen = [], t.index, set()
    while 0 <= i < len(code.types) and i not in seen:
        seen.add(i)
        chain.append(code.types[i])
        i = code.types[i].super_index
    for ct in chain:
        for p in ct.protos:
            protos.setdefault(p.name, {"findex": p.findex, "owner": ct.name})
    dump[n] = {"super": code.types[t.super_index].name
               if 0 <= t.super_index < len(code.types) else None,
               "fields": {k: {"offset": v[0], "kind": v[1], "type": v[2]}
                          for k, v in offs.items()},
               "methods": {k: v["findex"] for k, v in sorted(protos.items())},
               "method_owner": {k: v["owner"] for k, v in sorted(protos.items())}}

# ------------------------------------------------------------- function grep -
METHOD_RX = re.compile(
    r"(open|unlock|loot|reward|interact|activate|use|onUse|collect|pick|"
    r"grant|roll|drop|spawnLoot|giveLoot|onOpen|canOpen|isOpen|opened)",
    re.IGNORECASE)
hits = sorted((nm, fi) for fi, nm in names.items()
              if CLASS_RX.search(nm) and METHOD_RX.search(nm.split(".")[-1]))
print(f"\n=== chest/loot functions ({len(hits)}) ===")
for nm, fi in hits:
    print(f"  {fi:>7}  {nm}")

# Broader: any function whose *name* mentions chest/loot at all.
allhits = sorted((nm, fi) for fi, nm in names.items() if CLASS_RX.search(nm))
print(f"\n=== every function on a chest/loot class ({len(allhits)}) ===")
for nm, fi in allhits:
    print(f"  {fi:>7}  {nm}")

# ------------------------------------------------------------------ strings --
STR_RX = re.compile(r"(chest|lootbox|treasure|Opened|Closed|Locked)",
                    re.IGNORECASE)
strs = sorted({s for s in code.strings if STR_RX.search(s) and len(s) < 80})
print(f"\n=== interesting strings ({len(strs)}) ===")
for s in strs:
    print(f"  {s}")

payload = {"classes": classes, "detail": dump,
           "functions": {nm: fi for nm, fi in allhits},
           "strings": strs}
(OUT / "chest_survey.json").write_text(json.dumps(payload, indent=2),
                                       encoding="utf-8")
print(f"\n[written] {OUT / 'chest_survey.json'}")
