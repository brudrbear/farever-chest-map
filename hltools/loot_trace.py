"""Answer 'can item X actually drop from this loot table' straight from the CDB.

    py hltools/loot_trace.py Radiance
    py hltools/loot_trace.py Staff_Craft

Loot tables nest (a row can point at another lootTable instead of an item), so
reachability is a graph walk, not a lookup. This resolves the item by display
name or id, prints its own row, then reports every table that can reach it and
the full path + cumulative probability along the way.
"""
import json
import sys
from pathlib import Path

CDB = Path(__file__).resolve().parent.parent / "analysis_out" / "cdb" / "data.cdb"
if not CDB.exists():
    raise SystemExit(f"[!] {CDB} missing. Extract it first:\n"
                     "    py hltools/pak_extract.py extract data.cdb "
                     '--pak "...\\Farever\\res.light.pak" --out analysis_out/cdb')

d = json.loads(CDB.read_text(encoding="utf-8"))
sheets = {s["name"]: s for s in d["sheets"]}
items = {r["id"]: r for r in sheets["item"]["lines"]}
tables = {r["id"]: r for r in sheets["lootTable"]["lines"]}


def disp(item_id):
    r = items.get(item_id)
    if not r:
        return item_id
    t = r.get("texts") or {}
    return t.get("name") or item_id


def resolve(query):
    """Accept an item id or a display name, case-insensitively."""
    if query in items:
        return query
    q = query.casefold()
    hits = [i for i in items if i.casefold() == q]
    hits += [i for i, r in items.items()
             if ((r.get("texts") or {}).get("name") or "").casefold() == q]
    if not hits:
        hits = [i for i, r in items.items()
                if q in i.casefold()
                or q in ((r.get("texts") or {}).get("name") or "").casefold()]
    return hits[0] if len(hits) == 1 else (hits or None)


target = sys.argv[1] if len(sys.argv) > 1 else "Radiance"
res = resolve(target)
if res is None:
    raise SystemExit(f"[!] no item matches {target!r}")
if isinstance(res, list):
    print(f"[?] {target!r} is ambiguous:")
    for i in res[:25]:
        print(f"      {i:<34} {disp(i)}")
    raise SystemExit(1)

item = items[res]
print(f"=== item {res}  \"{disp(res)}\" ===")
for k in ("type", "rarity", "level", "iLevel", "faction", "sellPrice",
          "affinity"):
    if k in item:
        print(f"    {k:<12} {item[k]}")
props = item.get("props") or {}
if props:
    print(f"    props        {json.dumps(props)[:400]}")
print()

# ---- reachability over the loot-table graph --------------------------------
def rows(tid):
    return (tables.get(tid) or {}).get("loot") or []


def paths_to(tid, want, seen=None, prefix=(), proba=1.0):
    """Yield (path, cumulative_proba, row) for every way tid reaches want."""
    if seen is None:
        seen = set()
    if tid in seen:
        return
    seen = seen | {tid}
    for row in rows(tid):
        p = row.get("proba")
        p = float(p) if isinstance(p, (int, float)) else 1.0
        if row.get("item") == want:
            yield prefix + (tid,), proba * p, row
        sub = row.get("lootTable")
        if sub:
            yield from paths_to(sub, want, seen, prefix + (tid,), proba * p)


print(f"=== loot tables that can reach {res} ({len(tables)} tables scanned) ===")
found = 0
for tid in sorted(tables):
    for path, p, row in paths_to(tid, res):
        if len(path) > 1 and path[0] != tid:
            continue
        found += 1
        conds = row.get("conds")
        print(f"  {' -> '.join(path)}")
        print(f"      proba={row.get('proba')}  cumulative~{p:.6g}  "
              f"lvl[{row.get('minLvl')},{row.get('maxLvl')}]  "
              f"qty[{row.get('itemMin')},{row.get('itemMax')}]"
              + (f"  conds={json.dumps(conds)}" if conds else "")
              + (f"  flags={row.get('flags')}" if row.get("flags") else ""))
if not found:
    print("  NONE. No loot table in the CDB references this item, at any "
          "depth.")
print()

# ---- what DOES reference it, anywhere in the file --------------------------
print(f"=== every other mention of {res} in the CDB ===")
blob = CDB.read_text(encoding="utf-8")
import re
hits = 0
for s in d["sheets"]:
    txt = json.dumps(s.get("lines") or [])
    n = txt.count(f'"{res}"')
    if n:
        hits += n
        print(f"  {s['name']:<44} x{n}")
if not hits:
    print("  none")
