"""Sonda: dostane model zvuk vůbec? Vypíše, jak server zaznamenal vstup a co řekl model."""
import json, sys
from pathlib import Path
LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB))
from dabing.codex_client import Codex, audio, text, _slovnik

modely = sys.argv[1:] or ["gpt-5.6-terra"]
klip = LAB / "spike/out/promluva_01.ogg"
with Codex() as c:
    for m in modely:
        k = c.klient
        vl = k.thread_start({"ephemeral": True, "cwd": str(LAB / "codex_cwd"), "sandbox": "read-only",
                             "approvalPolicy": "never", "model": m}).thread.id
        t = k.turn_start(vl, [text("Přepiš přesně anglicky, co zazní v přiloženém zvuku. Pokud žádný zvuk nedostáváš, odpověz přesně NESLYSIM. Nepoužívej nástroje."),
                              audio(klip)], params={"effort": "low"}).turn.id
        k.register_turn_notifications(t)
        vstup, odpoved, udalosti = None, None, []
        while True:
            n = k.next_turn_notification(t)
            d = _slovnik(n.payload); udalosti.append(n.method)
            if n.method == "item/completed":
                it = d.get("item", {})
                if it.get("type") == "userMessage":
                    vstup = [{kk: (str(vv)[:80]) for kk, vv in x.items()} for x in it.get("content", [])]
                if it.get("type") == "agentMessage":
                    odpoved = it.get("text")
            if n.method == "turn/completed" and d.get("turn", {}).get("id") == t:
                break
        k.unregister_turn_notifications(t)
        print(f"\n=== {m}\n vstup zaznamenaný serverem: {json.dumps(vstup, ensure_ascii=False)}\n odpověď: {odpoved}\n události: {sorted(set(udalosti))}")
    aud = [l for l in c.stderr() if "audio" in l.lower() or "unsupported" in l.lower()]
    print("\nstderr (audio):", aud[-10:])
