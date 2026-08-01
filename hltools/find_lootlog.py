"""Find (a) the loot log the RegisterLootLog flag feeds, and (b) the element
state/scope machinery that decides whether a chest counts as already opened."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode                          # noqa: E402
from gamepath import find_hlboot                        # noqa: E402

code = HLCode(find_hlboot()).parse()
names = code.findex_names()
by_name = {t.name: t for t in code.obj_types()}

print("=== classes / functions mentioning LootLog ===")
for fi, nm in sorted(names.items(), key=lambda kv: kv[1]):
    if re.search(r"lootlog|loglooot|lootHistory|lootJournal", nm, re.I):
        print(f"  {fi:>7}  {nm}")
for t in code.obj_types():
    if re.search(r"lootlog", t.name, re.I):
        print(f"  CLASS {t.name}")
        for f in t.fields:
            print(f"        {f.name}: {code.type_str(f.type_index)}")

print("\n=== strings mentioning loot log / drop ===")
for s in sorted({s for s in code.strings
                 if re.search(r"lootlog|loot_log|LootLog", s, re.I)}):
    print(f"  {s}")

print("\n=== ent.Element: state / scope / activate methods ===")
t = by_name.get("ent.Element")
if t:
    for p in sorted(t.protos, key=lambda p: p.name):
        if re.search(r"(state|scope|activate|open|loot|interact|use|reset|"
                     r"respawn|refresh)", p.name, re.I):
            print(f"  {p.findex:>7}  {p.name}")

print("\n=== ElementState / ElementScope constants ===")
for t in code.types:
    if t.name and re.search(r"ElementState|ElementScope", t.name):
        print(f"  {t.name} (kind={t.kind})")
        for f in t.fields:
            print(f"      field {f.name}: {code.type_str(f.type_index)}")
        for c in t.constructs:
            print(f"      ctor  {c[0]}")

print("\n=== every ElementState string value in the pool ===")
cand = {"Opened", "Closed", "Locked", "Activated", "Desactivated", "Enabled",
        "Disabled", "Looted", "Empty", "Used"}
print("  present:", ", ".join(sorted(c for c in cand if c in code.strings)))

print("\n=== Interactible cooldown + scope-related functions (all classes) ===")
for fi, nm in sorted(names.items(), key=lambda kv: kv[1]):
    if re.search(r"(getInteractCooldown|elementScope|getScope|isPerPlayer|"
                 r"perPlayer|getElementState|setElementState|setState|"
                 r"onStateChange|canReactivate|reactivate)", nm, re.I):
        print(f"  {fi:>7}  {nm}")
