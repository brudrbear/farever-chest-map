"""Host for chest_probe.js — the chest-open discovery spike.

    py frida\\run_chest.py [seconds]

Attaches to a running Farever and answers, by measurement:

  * what fires (and in what order) when you open a chest;
  * where the "already opened" record is written — the probe hooks
    st.WorldContext.setElementState and prints the key and value rather than
    guessing at the StringMap layout;
  * whether the loot roll happens client-side at all
    (ent.Hero.generateLootItem / ent.Element.dropLootTable);
  * whether drop rates are independent — st.player.HeroData.worldLootLog is
    the bad-luck-protection ledger and its growth is watched.

Everything is resolved BY NAME from the live hlboot.dat at launch, and the run
aborts if analysis_out was generated from a different build. Hooks LOG ONLY:
nothing is called, no argument is replaced, no game memory is written.
"""
import json
import sys
import time
from pathlib import Path

import frida

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hltools"))
from hlbc_parser import HLCode                     # noqa: E402
from gamepath import find_hlboot                   # noqa: E402
from datafresh import assert_resolver_current      # noqa: E402

DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 900.0
OUT = HERE.parent / "analysis_out"
LOGDIR = HERE.parent / "logs"


def on_message(logfile):
    def handler(message, data):
        if message["type"] == "error":
            print("[JS ERROR]", message.get("description"), flush=True)
            return
        p = message.get("payload") or {}
        if p.get("kind") == "log":
            line = str(p["msg"])
            print(line.encode("ascii", "replace").decode(), flush=True)
            logfile.write(line + "\n")
            logfile.flush()
    return handler


def main():
    # Staleness gate: a probe that mixes fresh findices with stale anchors
    # fails SILENTLY (see hltools/datafresh.py), which is the worst outcome.
    code = HLCode(find_hlboot()).parse()
    assert_resolver_current(code, analysis_out=OUT)

    data = (OUT / "resolver_data.json").read_text(encoding="utf-8")
    p = json.loads((OUT / "chest_offsets.json").read_text(encoding="utf-8"))
    js = (HERE / "chest_probe.js").read_text(encoding="utf-8")

    if p["fn"]["postUpdate"] is None:
        raise SystemExit("[!] client.BaseCamera.postUpdate not found — no "
                         "game-thread anchor; aborting rather than risking the "
                         "GC-lock crash.")
    missing = sorted(k for k, v in p["hooks"].items() if v is None)
    if missing:
        print(f"[!] unresolved hooks (skipped): {missing}", flush=True)

    src = f"const DATA = {data};\nconst P = {json.dumps(p)};\n" + js

    LOGDIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    logpath = LOGDIR / f"chest-{stamp}.log"

    session = frida.attach("Farever.exe")
    with logpath.open("w", encoding="utf-8") as lf:
        script = session.create_script(src)
        script.on("message", on_message(lf))
        script.load()
        print(f"[*] attached, running {DURATION:.0f}s. Log -> {logpath}")
        print("[*] WAIT for 'PROBE ARMED' before touching a chest. Ctrl+C to "
              "stop early.", flush=True)
        try:
            time.sleep(DURATION)
        except KeyboardInterrupt:
            print("\n[*] stopping early.")
        finally:
            try:
                script.unload()
                session.detach()
            except Exception:
                pass
    print(f"[done] log written to {logpath}")


if __name__ == "__main__":
    main()
