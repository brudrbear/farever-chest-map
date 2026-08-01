"""Find where 'this chest was already opened' is recorded.

The Chest entity carries no opened flag (see chest_survey), so the record must
live somewhere else — player progress, a per-layer set, or the interaction
cooldown. This greps every FIELD on every class, plus function names, for that
vocabulary, and dumps the progress/loot-source classes in full.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT, HENUM   # noqa: E402
from gamepath import find_hlboot                        # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "analysis_out"
code = HLCode(find_hlboot()).parse()
names = code.findex_names()
by_name = {t.name: t for t in code.obj_types()}

FIELD_RX = re.compile(
    r"(opened|looted|used|consumed|activated|interacted|collected|claimed|"
    r"cooldown|lastUse|lastInteract|respawn|onetime|oneShot|unique)",
    re.IGNORECASE)

print("=== fields with 'already done' vocabulary on ent.*/st.* classes ===")
rows = []
for t in code.obj_types():
    if not re.match(r"^(ent|st|script|client)\.", t.name):
        continue
    for f in t.fields:
        if FIELD_RX.search(f.name):
            rows.append((t.name, f.name, code.type_str(f.type_index)))
for cls, fn, ty in sorted(rows):
    print(f"  {cls:<42} {fn:<28} {ty}")

# ---- enums that describe loot sources / chest state ----
print("\n=== enums mentioning loot/chest/state ===")
for t in code.types:
    if t.kind != HENUM or not t.name:
        continue
    if re.search(r"(Loot|Chest|Interact|Element|Reward)", t.name, re.I):
        print(f"  {t.name}: " + ", ".join(c[0] for c in t.constructs))

# ---- full dump of the progress-ish classes ----
PROGRESS_RX = re.compile(r"(Progress|Collection|AccountProgress|MapProgress|"
                         r"ZoneProgress|LootSource|Journal)", re.IGNORECASE)
targets = sorted(t.name for t in code.obj_types()
                 if PROGRESS_RX.search(t.name) and not t.name.startswith("_Data")
                 and "$" not in t.name)
print(f"\n=== progress-ish classes ({len(targets)}) ===")
out = {}
for n in targets:
    t = by_name[n]
    offs = code.field_offsets(t.index)
    meth = {p.name: p.findex for p in t.protos}
    out[n] = {"fields": {k: {"offset": v[0], "type": v[2]}
                         for k, v in offs.items()},
              "methods": meth}
    own = {f.name for f in t.fields}
    print(f"\n  --- {n} (own fields) ---")
    for k, v in offs.items():
        if k in own:
            print(f"    {v[0]:>6}  {k:<32} {v[2]}")
    hits = sorted(m for m in meth if FIELD_RX.search(m)
                  or re.search(r"(chest|loot|open)", m, re.I))
    if hits:
        print(f"    methods: {', '.join(hits)}")

# ---- the interaction cooldown path ----
print("\n=== interaction / activate functions on ent.Interactible ===")
t = by_name.get("ent.Interactible")
if t:
    for p in sorted(t.protos, key=lambda p: p.name):
        if re.search(r"(cooldown|activate|interact|use|check|can|request)",
                     p.name, re.I):
            print(f"  {p.findex:>7}  {p.name}")

(OUT / "open_record.json").write_text(
    json.dumps({"fields": rows, "progress": out}, indent=2), encoding="utf-8")
print(f"\n[written] {OUT / 'open_record.json'}")
