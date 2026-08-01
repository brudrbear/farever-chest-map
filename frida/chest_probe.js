// chest_probe.js — HOW DOES THE GAME KNOW A CHEST IS OPEN?
//
// Read-only. Every Interceptor here LOGS ONLY: nothing is called, no argument
// is replaced, nothing is written to game memory. The probe answers four
// questions and refuses to guess at any of them:
//
//   1. WHAT FIRES, IN WHAT ORDER, when you press the interact key on a chest.
//      46 functions along the interaction path are hooked and every fire is
//      appended to an ordered trace with a millisecond stamp. The trace is
//      printed as a block when the chest settles, so causality is visible
//      instead of inferred.
//
//   2. WHERE THE "ALREADY OPENED" RECORD LIVES. The Chest entity carries no
//      opened flag (it adds zero fields over ent.Element), and the meter
//      already measured that `stateId` does NOT move when you loot — only
//      `currentVisualState` flips Closed -> Opened, which is cosmetic. The
//      real candidate is st.WorldContext.elements, a StringMap, reachable
//      per-player as st.Player.playerContext. Rather than guess that map's
//      layout, the probe hooks WorldContext.getElementState /
//      setElementState / _setElementState__impl and prints their arguments —
//      the key and the value, straight from the game.
//
//   3. WHETHER THE CLIENT ROLLS THE LOOT. ent.Hero.generateLootItem /
//      resolveLootItem / makeLootItem and ent.Element.dropLootTable are
//      hooked. If they never fire on a real chest open, the roll is server
//      side and no client-side change can affect what drops.
//
//   4. WHAT THE BAD-LUCK-PROTECTION LOG DOES. st.player.HeroData.worldLootLog
//      is a replicated ArrayObj, and HeroData.lootLogContainsItem reads it.
//      Its length is watched every sweep, and the predicate is hooked, so a
//      drop-rate study can tell whether rolls are independent (they are not).
//
// THREAD RULE (this cost the meter project a live-game crash twice): any HL
// call from a frida timer thread dies with "Can't lock GC in unregistered
// thread". The only HL call in this file is the getHero lookup, and it runs
// inside the client.BaseCamera.postUpdate hook — i.e. on the game thread. The
// sweep timer does plain pointer/string reads and nothing else.
//
// DATA + P are prepended by run_chest.py.

function log(m) { send({ kind: "log", msg: String(m) }); }
function ptrPattern(addr){const b=[];let v=uint64(addr.toString());for(let i=0;i<8;i++){b.push(("0"+v.and(0xff).toNumber().toString(16)).slice(-2));v=v.shr(8);}return b.join(" ");}
function resolveAnchors(){const out=[],c={};for(const a of DATA.anchors){try{if(!(a.module in c))c[a.module]=Process.findModuleByName(a.module);const m=c[a.module];if(!m)continue;const ad=m.findExportByName(a.symbol);if(ad&&!ad.isNull())out.push({findex:a.findex,addr:ad});}catch(e){}}return out;}
function findTableBase(resolved){const ranges=Process.enumerateRanges("rw-").filter(r=>r.size<0x8000000);for(let s=0;s<Math.min(resolved.length,6);s++){const seed=resolved[s],pat=ptrPattern(seed.addr);for(const r of ranges){let mm;try{mm=Memory.scanSync(r.base,r.size,pat);}catch(e){continue;}for(const m of mm){const tb=m.address.sub(seed.findex*8);if(tb.compare(r.base)<0)continue;let agree=0,checked=0;for(const o of resolved){if(o===seed)continue;const slot=tb.add(o.findex*8);if(slot.compare(r.base)<0||slot.add(8).compare(r.base.add(r.size))>0)continue;checked++;let v;try{v=slot.readPointer();}catch(e){continue;}if(v.equals(o.addr))agree++;if(checked>=8)break;}if(agree>=3)return tb;}}}return null;}

function hlStr(p) {
    try {
        if (!p || p.isNull()) return null;
        const b = p.add(P.String.bytes).readPointer();
        if (b.isNull()) return null;
        return b.readUtf16String();
    } catch (e) { return null; }
}

const typeCache = {};
function typeName(p) {
    try {
        if (!p || p.isNull()) return null;
        const t = p.readPointer();
        const key = t.toString();
        const hit = typeCache[key];
        if (hit !== undefined) return hit;
        const k = t.readU32();
        let nm = null;
        if (k === 11 || k === 21)
            nm = t.add(8).readPointer().add(16).readPointer().readUtf16String();
        typeCache[key] = nm;
        return nm;
    } catch (e) { return null; }
}

function isElement(tn) {
    return tn && (tn === "ent.Element" ||
                  tn.lastIndexOf("ent.interactible.", 0) === 0);
}

// Describe an argument without assuming what it is. Elements print their kind
// and both state fields, because those are the whole question.
function describe(p) {
    try {
        if (!p || p.isNull()) return "null";
        if (p.compare(ptr("0x10000")) <= 0) return "int:" + p.toInt32();
        const tn = typeName(p);
        if (tn === "String") return "\"" + (hlStr(p) || "?") + "\"";
        if (isElement(tn)) {
            const kind = hlStr(p.add(P.Element.kind).readPointer());
            const sid = hlStr(p.add(P.Element.stateId).readPointer());
            const vis = hlStr(p.add(P.Element.currentVisualState).readPointer());
            let en = "?";
            try { en = p.add(P.Interactible.enabled).readU8(); } catch (x) {}
            return tn.replace("ent.interactible.", "") + "(" + kind +
                   " state=" + sid + " visual=" + vis + " enabled=" + en + ")";
        }
        if (tn === "ent.Hero") {
            let nm = null;
            try { nm = hlStr(p.add(P.Hero.name).readPointer()); } catch (x) {}
            return "Hero(" + (nm || "?") + ")";
        }
        if (tn === "st.Player") {
            let nm = null;
            try { nm = hlStr(p.add(P.Player.name).readPointer()); } catch (x) {}
            return "Player(" + (nm || "?") + ")";
        }
        if (tn === "st.WorldContext") return "WorldContext";
        if (tn) return tn;
        return p.toString();
    } catch (e) { return "err:" + e.message; }
}

// ---- the ordered trace -----------------------------------------------------
// Every hook fire lands here. The trace is what makes the ordering claim
// falsifiable: it is printed verbatim, not summarised into a story.
let trace = [];
let traceOpenedAt = 0;
const fireCount = {};
let armed = false;
const t0 = Date.now();

function now() { return Date.now() - t0; }

function record(name, args, argc) {
    fireCount[name] = (fireCount[name] || 0) + 1;
    if (!armed) return;
    let line = name;
    const parts = [];
    for (let i = 0; i <= argc; i++) {
        try { parts.push((i === 0 ? "this=" : "a" + i + "=") + describe(args[i])); }
        catch (e) { break; }
    }
    if (parts.length) line += "  " + parts.join("  ");
    // Bounded: a hot hook must not be able to push the interesting ones out.
    if (trace.length < 400) trace.push({ t: now(), line: line });
    if (!traceOpenedAt) traceOpenedAt = now();
}

// A few of these are per-frame or per-proximity-tick rather than per-press.
// They stay hooked (their ABSENCE from a trace is also evidence) but only
// count, so they cannot drown the trace.
const COUNTER_ONLY = {
    "PlayerController.canActivateInteractible": true,
    "PlayerController.getClosestInteractible": true,
    "Interactible.canActivate": true,
    "Interactible.canInteract": true,
    "Element.getElementState": true,
    "Element.getPlayerState": true,
    "Element.hasStateFlag": true,
    "Interactible.getInteractCooldown": true,
    "Element.getUseCooldown": true,
    "Element.updateStateVisual": true,
};

function attachLogged(name, findex) {
    if (findex == null) { log("!! no findex for " + name + " — skipped"); return; }
    let addr;
    try { addr = base.add(findex * 8).readPointer(); }
    catch (e) { log("!! " + name + " unreadable: " + e.message); return; }
    const counterOnly = !!COUNTER_ONLY[name];
    Interceptor.attach(addr, {
        onEnter: function (args) {
            if (counterOnly) { fireCount[name] = (fireCount[name] || 0) + 1; return; }
            record(name, args, 3);
        }
    });
}

// ---- local hero (GAME THREAD ONLY) ----------------------------------------
let base = null, localHero = null, frames = 0;

function refreshLocalHeroOnGameThread() {
    if (localHero && !localHero.isNull()) return;
    for (const nm in DATA.funcs) {
        try {
            const h = new NativeFunction(base.add(DATA.funcs[nm] * 8).readPointer(),
                                         "pointer", [])();
            if (h && !h.isNull() && typeName(h) === "ent.Hero") {
                localHero = h;
                return;
            }
        } catch (e) {}
    }
}

// ---- plain-read sweep ------------------------------------------------------
let ticks = 0;
let lastLootLogLen = -1;
let lastInvLen = -1;
const watched = {};      // element ptr -> last "state|visual|enabled"
let lastCounts = "";

function arrayObjLen(p) {
    try {
        if (!p || p.isNull()) return -1;
        return p.add(P.ArrayObj.length).readS32();
    } catch (e) { return -1; }
}

function proxyArrayLen(proxy) {
    try {
        if (!proxy || proxy.isNull()) return -1;
        const dyn = proxy.add(P.ArrayProxyData.array).readPointer();
        if (!dyn || dyn.isNull()) return -1;
        const inner = dyn.add(P.ArrayDyn.array).readPointer();
        return arrayObjLen(inner);
    } catch (e) { return -1; }
}

function heroDataPtr() {
    try {
        const pl = localHero.add(P.Hero.player).readPointer();
        if (!pl || pl.isNull()) return null;
        return pl.add(P.Player.heroData).readPointer();
    } catch (e) { return null; }
}

function inventoryLen() {
    try {
        const ld = localHero.add(P.Hero.loadout).readPointer();
        if (!ld || ld.isNull()) return -1;
        const inv = ld.add(P.Loadout.inventory).readPointer();
        if (!inv || inv.isNull()) return -1;
        return proxyArrayLen(inv.add(P.Inventory.content).readPointer());
    } catch (e) { return -1; }
}

// Walk the layer's interactibles and report every Chest whose state fields
// move. This is the ground truth the hook trace is checked against.
function sweepChests() {
    const layer = localHero.add(P.Hero.layer).readPointer();
    if (!layer || layer.isNull()) return;
    const arr = layer.add(P.GameLayer.interactibles).readPointer();
    if (!arr || arr.isNull()) return;
    const n = arrayObjLen(arr);
    if (n < 0 || n > 8000) return;
    const data = arr.add(P.ArrayObj.array).readPointer();
    if (!data || data.isNull()) return;
    let chests = 0;
    for (let i = 0; i < n; i++) {
        let e;
        try { e = data.add(P.ArrayObj.data + i * 8).readPointer(); } catch (x) { continue; }
        if (!e || e.isNull() || e.compare(ptr("0x10000")) <= 0) continue;
        if (typeName(e) !== "ent.interactible.Chest") continue;
        chests++;
        let sid, vis, en;
        try {
            sid = hlStr(e.add(P.Element.stateId).readPointer());
            vis = hlStr(e.add(P.Element.currentVisualState).readPointer());
            en = e.add(P.Interactible.enabled).readU8();
        } catch (x) { continue; }
        const sig = sid + "|" + vis + "|" + en;
        const key = e.toString();
        const prev = watched[key];
        if (prev === undefined) { watched[key] = sig; continue; }
        if (prev !== sig) {
            watched[key] = sig;
            const kind = hlStr(e.add(P.Element.kind).readPointer());
            log("");
            log("=== CHEST STATE CHANGED  " + kind + " @" + e);
            log("    " + prev + "   ->   " + sig
                + "     (stateId|currentVisualState|enabled)");
            dumpTrace();
        }
    }
    if (ticks === 2) log("[sweep] " + chests + " chests visible in this layer");
}

function dumpTrace() {
    if (!trace.length) {
        log("    trace: EMPTY — no hooked function fired. That is a result, "
            + "not a failure: it means nothing on the hooked client path "
            + "participated in this change.");
        return;
    }
    log("    --- ordered trace (" + trace.length + " entries, ms since start) ---");
    for (const ev of trace) log("      " + String(ev.t).padStart(7) + "  " + ev.line);
    log("    --- end trace ---");
    trace = [];
    traceOpenedAt = 0;
}

function sweep() {
    try {
        ticks++;
        if (!localHero || localHero.isNull()) {
            if (ticks % 20 === 0)
                log("[wait] frames=" + frames + " — no ent.Hero yet "
                    + "(menu/loading is expected).");
            return;
        }
        if (!armed) {
            armed = true;
            log("");
            log("PROBE ARMED — localHero=" + localHero + " after " + frames
                + " frames.");
            log("Walk to a chest and open it. Then try to open the SAME chest "
                + "again and let the probe watch what refuses.");
            log("");
        }

        sweepChests();

        // Bad-luck-protection log: growth means the roll is not independent.
        const hd = heroDataPtr();
        if (hd && !hd.isNull()) {
            const n = arrayObjLen(hd.add(P.HeroData.worldLootLog).readPointer());
            if (n !== lastLootLogLen) {
                if (lastLootLogLen >= 0)
                    log("=== worldLootLog length: " + lastLootLogLen + " -> " + n
                        + "   (bad-luck-protection ledger grew)");
                lastLootLogLen = n;
            }
        }

        const inv = inventoryLen();
        if (inv !== lastInvLen) {
            if (lastInvLen >= 0)
                log("=== inventory slots: " + lastInvLen + " -> " + inv);
            lastInvLen = inv;
        }

        // If hooks fired but no chest state moved, still show the trace —
        // a refused re-open looks exactly like this.
        if (trace.length && now() - traceOpenedAt > 2500) {
            log("");
            log("=== interaction trace with NO chest state change "
                + "(refused, or a non-chest interactible)");
            dumpTrace();
        }

        if (ticks % 20 === 0) {
            const parts = [];
            for (const k in fireCount) parts.push(k + " x" + fireCount[k]);
            const line = parts.sort().join("  ");
            if (line && line !== lastCounts) {
                log("[counts] " + line);
                lastCounts = line;
            }
        }
    } catch (e) { log("sweep ERR " + e); }
}

function main() {
    base = findTableBase(resolveAnchors());
    if (!base) { log("!! functions_ptrs table not found"); return; }
    log("table base " + base);
    if (P.fn.postUpdate == null) {
        log("!! no postUpdate findex — no game-thread anchor; refusing.");
        return;
    }
    Interceptor.attach(base.add(P.fn.postUpdate * 8).readPointer(), {
        onEnter: function () { frames++; refreshLocalHeroOnGameThread(); }
    });
    let n = 0;
    for (const name in P.hooks) {
        if (P.hooks[name] != null) n++;
        attachLogged(name, P.hooks[name]);
    }
    log("hooked " + n + " functions (log-only). Waiting for the hero — "
        + "nothing is armed until the ARMED line prints.");
    setInterval(sweep, 400);
}

setTimeout(main, 0);
