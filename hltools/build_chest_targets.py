"""Emit resolver_data.json + chest_offsets.json for the chest probe.

Same contract as FareverMeter's build_targets.py: everything is resolved BY
NAME from the live hlboot.dat, and the emitted `nfunctions`/`nnatives` let
datafresh.py refuse to attach against a stale file after a game patch.

    py hltools\\build_chest_targets.py [path\\to\\hlboot.dat]
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from hlbc_parser import HLCode, HOBJ, HSTRUCT      # noqa: E402
from gamepath import find_hlboot                   # noqa: E402

OUT = Path(os.environ.get("FAREVER_ANALYSIS_OUT")
           or Path(__file__).resolve().parent.parent / "analysis_out")
OUT.mkdir(parents=True, exist_ok=True)

HLBOOT = find_hlboot()
print(f"[*] parsing {HLBOOT}")
code = HLCode(HLBOOT).parse()
names = code.findex_names()
byname = {t.name: t for t in code.types if t.kind in (HOBJ, HSTRUCT) and t.name}

# ---- native anchors that triangulate the functions_ptrs table base ----------
HDLL_LIBS = {"ssl", "fmt", "uv", "ui", "sdl", "directx", "dx12", "openal",
             "heaps", "steam", "video", "hlfmod", "mysql", "dlss"}
anchors = [{"lib": n.lib, "name": n.name, "findex": n.findex,
            "symbol": f"{n.lib}_{n.name}", "module": f"{n.lib}.hdll"}
           for n in code.natives if n.lib in HDLL_LIBS][:40]

SINGLETON_FNS = ["GameApp.getCameraHero", "ui.Console.getMyHero",
                 "$GameApp.getMyHero", "$GameApp.get"]
funcs = {nm.lstrip("$"): fi for fi, nm in names.items() if nm in SINGLETON_FNS}


def proto(cls, meth):
    t = byname.get(cls)
    return next((x.findex for x in t.protos if x.name == meth), None) if t else None


def offs(cls, *want):
    t = byname.get(cls)
    if not t:
        return {}
    o = code.field_offsets(t.index)
    return {f: o[f][0] for f in want if f in o}


# ---- the client-side chest interaction path --------------------------------
# Grouped by the question each hook answers. Log-only in the probe.
HOOKS = {
    # 1. the local request — what the client sends when you press the key
    "PlayerController.canActivateInteractible":
        proto("client.PlayerController", "canActivateInteractible"),
    "PlayerController.getClosestInteractible":
        proto("client.PlayerController", "getClosestInteractible"),
    "Interactible.tryRequestInteraction":
        proto("ent.Interactible", "tryRequestInteraction"),
    "Interactible.requestInteraction":
        proto("ent.Interactible", "requestInteraction"),
    "Interactible.requestActivate":
        proto("ent.Interactible", "requestActivate"),
    "Interactible.requestClientInteraction":
        proto("ent.Interactible", "requestClientInteraction"),
    "Interactible.checkRequestInteraction":
        proto("ent.Interactible", "checkRequestInteraction"),
    "Interactible.checkRequestActivate":
        proto("ent.Interactible", "checkRequestActivate"),
    "Interactible.canRequestActivate":
        proto("ent.Interactible", "canRequestActivate"),
    "Interactible.canActivate": proto("ent.Interactible", "canActivate"),
    "Interactible.canInteract": proto("ent.Interactible", "canInteract"),
    "Interactible.checkUse": proto("ent.Interactible", "checkUse"),
    "Interactible.getInteractCooldown":
        proto("ent.Interactible", "getInteractCooldown"),

    # 2. the RPC bodies — these run wherever the authority is
    "Interactible.apiRpcRequestInteraction":
        proto("ent.Interactible", "apiRpcRequestInteraction"),
    "Interactible.apiRpcRequestInteraction__impl":
        proto("ent.Interactible", "apiRpcRequestInteraction__impl"),
    "Interactible.apiRpcRequestActivate":
        proto("ent.Interactible", "apiRpcRequestActivate"),
    "Interactible.apiRpcRequestActivate__impl":
        proto("ent.Interactible", "apiRpcRequestActivate__impl"),
    "Interactible.implRequestInteraction":
        proto("ent.Interactible", "implRequestInteraction"),
    "Interactible.doPlayerActivate":
        proto("ent.Interactible", "doPlayerActivate"),
    "Interactible.doPlayerActivateClient":
        proto("ent.Interactible", "doPlayerActivateClient"),
    "Interactible.onPlayerActivate":
        proto("ent.Interactible", "onPlayerActivate"),

    # 3. the chest itself — its ONLY gameplay method
    "Chest.onPlayerActivate":
        proto("ent.interactible.Chest", "onPlayerActivate"),

    # 4. the element state machine — the "is it open" record
    "Element.getElementState": proto("ent.Element", "getElementState"),
    "Element.setElementState": proto("ent.Element", "setElementState"),
    "Element.getPlayerState": proto("ent.Element", "getPlayerState"),
    "Element.hasStateFlag": proto("ent.Element", "hasStateFlag"),
    "Element.processStateAction": proto("ent.Element", "processStateAction"),
    "Element.set_stateId": proto("ent.Element", "set_stateId"),
    "Element.updateStateVisual": proto("ent.Element", "updateStateVisual"),
    "Element.netUpdateStateVisual__impl":
        proto("ent.Element", "netUpdateStateVisual__impl"),
    "Element.rpcPlayStateVisual__impl":
        proto("ent.Element", "rpcPlayStateVisual__impl"),
    "Element.getUseCooldown": proto("ent.Element", "getUseCooldown"),
    "Element.dropLootTable": proto("ent.Element", "dropLootTable"),
    "Element.getLootInfo": proto("ent.Element", "getLootInfo"),

    # 5. the world-context ledger — where opened-ness is actually stored
    "WorldContext.getElementState": proto("st.WorldContext", "getElementState"),
    "WorldContext.setElementState": proto("st.WorldContext", "setElementState"),
    "WorldContext._setElementState__impl":
        proto("st.WorldContext", "_setElementState__impl"),
    "WorldContext.resetElementState":
        proto("st.WorldContext", "resetElementState"),

    # 6. loot arrival + the bad-luck-protection log
    "HeroData.lootLogContainsItem":
        proto("st.player.HeroData", "lootLogContainsItem"),
    "HeroData.set_worldLootLog": proto("st.player.HeroData", "set_worldLootLog"),
    "Hero.generateLootItem": proto("ent.Hero", "generateLootItem"),
    "Hero.generateWorldLootItem": proto("ent.Hero", "generateWorldLootItem"),
    "Hero.resolveLootItem": proto("ent.Hero", "resolveLootItem"),
    "Hero.makeLootItem": proto("ent.Hero", "makeLootItem"),
    "Loadout.addItem": proto("st.Loadout", "addItem"),
    "Progress.incrementItemLoot":
        proto("st.player.Progress", "incrementItemLoot"),
}

P = {
    "fn": {"postUpdate": proto("client.BaseCamera", "postUpdate")},
    "hooks": HOOKS,
    "Hero": offs("ent.Hero", "player", "name", "loadout", "layer"),
    "Player": offs("st.Player", "name", "isMe", "heroData", "progress",
                   "playerContext", "lastInteract", "hero", "accountProgress"),
    "HeroData": offs("st.player.HeroData", "worldLootLog", "level", "name"),
    "WorldContext": offs("st.WorldContext", "elements"),
    "Loadout": offs("st.Loadout", "inventory", "equipment"),
    "Inventory": offs("st.Inventory", "content"),
    "Item": offs("st.Item", "kind", "__uid"),
    "Element": offs("ent.Element", "kind", "stateId", "currentVisualState",
                    "inf", "script"),
    "Interactible": offs("ent.Interactible", "enabled", "isOffScreen",
                         "lastRequest"),
    "Entity": offs("ent.Entity", "posx", "posy", "posz"),
    "GameLayer": offs("st.GameLayer", "interactibles", "units", "entities"),
    "String": offs("String", "bytes", "length"),
    "ArrayObj": offs("hl.types.ArrayObj", "length", "array"),
    "ArrayProxyData": offs("hxbit.ArrayProxyData", "array"),
    "ArrayDyn": offs("hl.types.ArrayDyn", "array"),
    "StringMap": offs("haxe.ds.StringMap", "h"),
}
# ArrayObj payload starts 8 bytes past the `array` pointer field (varray header).
P["ArrayObj"]["data"] = 24

resolver = {
    "nfunctions": code.counts["nfunctions"],
    "nnatives": code.counts["nnatives"],
    "anchors": anchors,
    "funcs": funcs,
}
(OUT / "resolver_data.json").write_text(json.dumps(resolver, indent=2),
                                        encoding="utf-8")
(OUT / "chest_offsets.json").write_text(json.dumps(P, indent=2),
                                        encoding="utf-8")

missing = sorted(k for k, v in HOOKS.items() if v is None)
print(f"    anchors={len(anchors)} funcs={len(funcs)} "
      f"hooks={len(HOOKS) - len(missing)}/{len(HOOKS)}")
if missing:
    print(f"    [!] unresolved (skipped at attach): {missing}")
for grp in ("Hero", "Player", "HeroData", "WorldContext", "Element",
            "Interactible", "GameLayer"):
    print(f"    {grp:<14} {P[grp]}")
print(f"[written] {OUT / 'resolver_data.json'}\n[written] "
      f"{OUT / 'chest_offsets.json'}")
