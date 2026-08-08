"""Build the standalone chest map report from the game's own data.

Maintainer one-shot, like build_map_assets.py in the meter: run it after a
Farever patch, ship the HTML it writes. The page reads nothing at runtime —
map image, transform, placements and loot tables are all baked in.

    py hltools/build_chest_report.py [--out report/chest_map.html]

Three inputs, all game-derived:

  * `analysis_out/prefabs_map/*.prefab` — the world's placed elements, parsed
    with hbson.py (see its docstring for the interned-string trap). Every
    element/activity that carries a `props.lootTable` is emitted with the
    world x/y/z of the object node that holds it.
  * `analysis_out/cdb/data.cdb` — the `lootTable` sheet, flattened per table so
    the page can show what each chest actually pays. Nested tables are walked;
    `Weights` (flags bit 1) means the table picks exactly ONE row weighted by
    proba, everything else rolls independently, and the same item arriving by
    two paths is combined as 1-(1-a)(1-b).
  * `assets/maps/w1_siagarta.{webp,json}` — the stitched minimap tiles and the
    world->pixel transform (576 world units per 1024 px tile, +y drawn DOWN),
    written by `build_map_assets.py`, which this runs for you if the asset is
    missing. Markers are emitted as fractions of the image so the downscale
    here cannot desync them.

Regenerate the loot tables first if the game has patched:

    py hltools/pak_extract.py extract data.cdb --pak <...>\\res.light.pak \\
        --out analysis_out/cdb
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

import build_map_assets
import hbson

ROOT = Path(__file__).resolve().parent.parent
PREFABS = ROOT / "analysis_out" / "prefabs_map"
CDB = ROOT / "analysis_out" / "cdb" / "data.cdb"
MAPS = ROOT / "assets" / "maps"
WORLD = "w1_siagarta"
MAP_PX = 3584            # downscale of the 5632 px stitch, for page weight
MAP_QUALITY = 80


# --- placements --------------------------------------------------------------

def collect_placements():
    rows = []

    def rnd(v):
        return round(v, 2) if isinstance(v, (int, float)) else None

    def walk(node, src=None, xyz=None):
        if isinstance(node, list):
            for n in node:
                walk(n, src, xyz)
            return
        if not isinstance(node, dict):
            return
        if isinstance(node.get("source"), str):
            src = node["source"]
        if isinstance(node.get("x"), (int, float)):
            xyz = (node.get("x"), node.get("y"), node.get("z"))
        if "$cdbtype" in node:
            inner = node.get("props")
            table = inner.get("lootTable") if isinstance(inner, dict) else None
            if table and xyz:
                rows.append({
                    "id": node.get("id"),
                    "kind": node.get("$cdbtype"),
                    "inherit": node.get("inherit"),
                    "table": table,
                    "faction": inner.get("faction"),
                    "level": inner.get("level"),
                    # a placement can pin extra items on top of its table —
                    # 19 chests do, and those are the hand-placed rewards
                    "unique": [i for i in (inner.get("lootItems") or [])
                               if i.get("item")],
                    "zone": node.get("zoneBaked"),
                    "label": (node.get("texts") or {}).get("name"),
                    "src": (src or "").split("/")[-1].replace(".prefab", ""),
                    "x": rnd(xyz[0]), "y": rnd(xyz[1]), "z": rnd(xyz[2]),
                })
        for v in node.values():
            if isinstance(v, (list, dict)):
                walk(v, src, xyz)

    files = sorted(PREFABS.glob("*.prefab"))
    if not files:
        sys.exit(f"[!] no prefabs in {PREFABS}")
    for p in files:
        walk(hbson.load_strict(p.read_bytes(), p.name))
    print(f"[*] {len(files)} prefabs -> {len(rows)} placements with a lootTable",
          file=sys.stderr)
    return rows


# --- loot tables -------------------------------------------------------------

def load_tables():
    if not CDB.exists():
        sys.exit(f"[!] {CDB} missing — extract data.cdb from res.light.pak")
    d = json.loads(CDB.read_text(encoding="utf-8"))
    sheets = {s["name"]: s for s in d["sheets"]}
    return ({r["id"]: r for r in sheets["lootTable"]["lines"]},
            {r["id"]: r for r in sheets["item"]["lines"]})


CONDS = {1: "BasicFoe", 2: "SpecialFoe", 4: "DungeonFoe"}
REF = __import__("re").compile(r"::ref_[^:]+::")


def item_name(iid, items):
    """Display name, with the CDB's ::ref_x:: text placeholders stripped."""
    raw = ((items.get(iid) or {}).get("texts") or {}).get("name") or ""
    clean = REF.sub("", raw).strip(" :·-")
    return clean or iid


def flatten(tid, tables, items, seen=None, proba=1.0, out=None):
    out = [] if out is None else out
    seen = (seen or set()) | {tid}
    t = tables.get(tid) or {}
    rows = t.get("loot") or []
    weighted = bool((t.get("flags") or 0) & 1)
    total = sum(float(r.get("proba") or 0) for r in rows) or 1.0
    for row in rows:
        p = row.get("proba")
        p = float(p) if isinstance(p, (int, float)) else 1.0
        share = (p / total) if weighted else p
        if row.get("item"):
            iid = row["item"]
            out.append({
                "id": iid,
                "name": item_name(iid, items),
                "p": round(proba * share, 6),
                "qty": ([row.get("itemMin"), row.get("itemMax")]
                        if row.get("itemMax") else None),
                "conds": "+".join(n for b, n in CONDS.items()
                                  if (row.get("conds") or 0) & b) or None,
            })
        sub = row.get("lootTable")
        if sub and sub not in seen:
            flatten(sub, tables, items, seen, proba * share, out)
    return out


def loot_summary(used, tables, items):
    out = {}
    for tid in sorted(used):
        merged = {}
        for leaf in flatten(tid, tables, items):
            prev = merged.get(leaf["id"])
            if prev is None:
                merged[leaf["id"]] = dict(leaf)
            else:                       # same item, two independent paths
                prev["p"] = round(1 - (1 - prev["p"]) * (1 - leaf["p"]), 6)
        rows = sorted(merged.values(), key=lambda r: -r["p"])
        out[tid] = {
            "rows": rows,
            "world": round(sum(r["p"] for r in rows
                               if r["id"].startswith("WorldLoot")), 4),
            "flags": (tables.get(tid) or {}).get("flags") or 0,
        }
    return out


# --- map ---------------------------------------------------------------------

def map_asset():
    img, meta = MAPS / f"{WORLD}.webp", MAPS / f"{WORLD}.json"
    if not (img.exists() and meta.exists()):
        print(f"[*] {MAPS} has no {WORLD} yet — stitching it from res.map.pak",
              file=sys.stderr)
        from gamepath import find_pak
        build_map_assets.build(find_pak("res.map.pak"), WORLD, 0.5)
    tf = json.loads(meta.read_text(encoding="utf-8"))
    im = Image.open(img).convert("RGB")
    im = im.resize((MAP_PX, MAP_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=MAP_QUALITY, method=6)
    b = buf.getvalue()
    print(f"[*] map {MAP_PX}px webp {len(b) // 1024} KB", file=sys.stderr)
    return tf, "data:image/webp;base64," + base64.b64encode(b).decode("ascii")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "report" / "chest_map.html"))
    args = ap.parse_args()

    placements = collect_placements()
    tables, items = load_tables()
    loot = loot_summary({r["table"] for r in placements}, tables, items)
    tf, map_uri = map_asset()

    # world -> fraction of the map image; the transform is written for the
    # 5632 px stitch, and fractions survive any downscale.
    span = tf["width"] / tf["px_per_unit"]
    for r in placements:
        r["fx"] = round((r["x"] - tf["origin_x"]) / span, 6)
        r["fy"] = round((r["y"] - tf["origin_y"]) / span, 6)

    chests = [r for r in placements if r["kind"] == "element"]
    tokened = sum(1 for r in chests if loot[r["table"]]["world"])
    print(f"[*] {len(chests)} chests ({tokened} with a WorldLoot roll), "
          f"{len(placements) - len(chests)} activities", file=sys.stderr)

    # The WorldLoot token is expanded server-side out of the `faction: World`
    # items ($HItem.isWorldLoot / getFactionLootTable). The pool is readable
    # here; the weighting inside it is not — that code never runs client-side.
    pool = [{"id": r["id"], "name": item_name(r["id"], items),
             "type": r.get("type"), "level": r.get("level"),
             "rarity": r.get("rarity")}
            for r in items.values() if r.get("faction") == "World"]

    # resolve the hand-placed items, incl. what a `Package` asks to be filled
    for r in placements:
        for u in r["unique"]:
            src_item = items.get(u["item"]) or {}
            u["name"] = item_name(u["item"], items)
            u["type"] = src_item.get("type")
            u["rarity"] = src_item.get("rarity")
            comp = (src_item.get("props") or {}).get("completable") or {}
            if comp:
                u["needs"] = [f'{c.get("amount")}× {item_name(c.get("kind"), items)}'
                              for c in comp.get("require") or []]
                u["gives"] = [f'{c.get("amount")}× {item_name(c.get("kind"), items)}'
                              for c in comp.get("reward") or []]
    n_uniq = sum(1 for r in placements if r["unique"] and r["kind"] == "element")
    print(f"[*] {n_uniq} chests carry a hand-placed item", file=sys.stderr)

    # Stamp what this was built from, so a stale page says so on its face.
    cdb_sha = hashlib.sha256(CDB.read_bytes()).hexdigest()[:12]
    data = {"placements": placements, "loot": loot, "pool": pool,
            "meta": {"world": WORLD, "built": datetime.now().strftime("%Y-%m-%d"),
                     "chests": len(chests), "activities": len(placements) - len(chests),
                     "cdb_sha": cdb_sha, "map_px": MAP_PX}}
    tpl = (Path(__file__).parent / "chest_report.template.html").read_text(
        encoding="utf-8")
    html = (tpl.replace("__MAP_SRC__", map_uri)
               .replace("'__DATA__'", json.dumps(data, separators=(",", ":"))))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"[+] {out}  ({out.stat().st_size // 1024} KB)", file=sys.stderr)


if __name__ == "__main__":
    main()
