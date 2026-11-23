# ruff: noqa: E501
"""Panneau HAUTE DISPONIBILITÉ (interface web locale) : http://localhost:8090

Un bouton « Couper ce serveur » supprime brutalement la VM GKE (panne réelle),
pendant qu'une sonde interroge le site 10 fois par seconde. On voit en direct :
serveurs, pods, réussites/échecs par seconde, disponibilité, temps de rétablissement.

Lancement : .venv/bin/python scripts/ha-panel.py   (ou via scripts/jury.sh)
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(line.split("=", 1) for line in (ROOT / ".env").read_text().split() if "=" in line)
ZONE = os.getenv("ZONE", ENV.get("ZONE", "europe-west1-b"))
PROJECT = os.getenv("PROJECT_ID", ENV.get("PROJECT_ID", ""))
PORT = int(os.getenv("PANEL_PORT", "8090"))

lock = threading.Lock()
state: dict = {"nodes": [], "pods": [], "desired": 0, "available": 0, "url": ""}
seconds: deque = deque(maxlen=120)  # (epoch_s, ok, ko, pods)
events: list = []
incident: dict = {}


def event(msg: str) -> None:
    with lock:
        events.append({"t": time.time(), "msg": msg})


def kubectl(*args: str) -> dict:
    out = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, timeout=20)
    return json.loads(out.stdout) if out.returncode == 0 else {}


def poll_cluster() -> None:
    while True:
        try:
            nodes = kubectl("get", "nodes").get("items", [])
            pods = kubectl("-n", "lab", "get", "pods", "-l", "app.kubernetes.io/name=webapp").get("items", [])
            dep = kubectl("-n", "lab", "get", "deployment", "webapp")
            svc = kubectl("-n", "lab", "get", "svc", "webapp")
            ns = [
                {
                    "name": n["metadata"]["name"],
                    "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"]),
                    "age": n["metadata"]["creationTimestamp"],
                }
                for n in nodes
            ]
            ps = [
                {
                    "name": p["metadata"]["name"],
                    "node": p["spec"].get("nodeName", ""),
                    "phase": p["status"].get("phase", ""),
                    "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", [])),
                    "deleting": "deletionTimestamp" in p["metadata"],
                }
                for p in pods
            ]
            desired = dep.get("spec", {}).get("replicas", 0)
            available = dep.get("status", {}).get("availableReplicas", 0) or 0
            ip = (svc.get("status", {}).get("loadBalancer", {}).get("ingress") or [{}])[0].get("ip", "")
            with lock:
                state.update(nodes=ns, pods=ps, desired=desired, available=available, url=f"http://{ip}/" if ip else "")
                track_incident(ns, desired, available)
        except Exception as exc:  # noqa: BLE001 — le panneau ne doit jamais s'arrêter
            print("poll:", exc)
        time.sleep(2)


def track_incident(nodes: list, desired: int, available: int) -> None:
    """Appelé sous verrou : détecte nœud perdu, pods rétablis, nouveau nœud prêt."""
    if not incident or incident.get("done"):
        return
    now = time.time()
    victim = incident["node"]
    names = {n["name"]: n for n in nodes}
    if "node_lost" not in incident and (victim not in names or not names[victim]["ready"]):
        incident["node_lost"] = now
        events.append({"t": now, "msg": f"🔴 Kubernetes détecte la perte du serveur {victim[-4:]}"})
    if available < desired:
        incident["degraded"] = True
    if "pods_ok" not in incident and incident.get("degraded") and available >= desired:
        incident["pods_ok"] = now
        events.append({"t": now, "msg": f"🟢 Tous les pods sont rétablis ({available}/{desired}) sur les serveurs restants"})
    ready = sum(n["ready"] for n in nodes)
    if "node_back" not in incident and incident.get("node_lost") and ready >= incident["nodes_before"]:
        incident["node_back"] = now
        incident["done"] = True
        events.append({"t": now, "msg": "🟢 GKE a recréé le serveur : le cluster est revenu à sa taille normale"})


cnt = {"ok": 0, "ko": 0, "pods": set()}


def probe_worker() -> None:
    """Un « client » : 2 requêtes/s, chacune sur une nouvelle connexion TCP."""
    while True:
        t = time.time()
        try:
            req = urllib.request.Request(state["url"], headers={"Connection": "close", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as r:
                pod = json.loads(r.read()).get("pod", "?")
            with lock:
                cnt["ok"] += 1
                cnt["pods"].add(pod)
        except Exception:  # noqa: BLE001
            with lock:
                cnt["ko"] += 1
        time.sleep(max(0, 0.5 - (time.time() - t)))


def probe() -> None:
    """5 clients en parallèle (≈ 10 req/s) ; agrège les résultats seconde par seconde."""
    while not state["url"]:
        time.sleep(0.5)
    for _ in range(5):
        threading.Thread(target=probe_worker, daemon=True).start()
    while True:
        cur = int(time.time())
        time.sleep(cur + 1 - time.time())
        with lock:
            ok, ko, pods = cnt["ok"], cnt["ko"], sorted(cnt["pods"])
            cnt.update(ok=0, ko=0, pods=set())
            seconds.append((cur, ok, ko, pods))
            if incident.get("t") and not incident.get("done"):
                incident["ok"] = incident.get("ok", 0) + ok
                incident["ko"] = incident.get("ko", 0) + ko


def kill(node: str) -> str:
    with lock:
        if incident and not incident.get("done"):
            return "Une panne est déjà en cours"
        if node not in {n["name"] for n in state["nodes"]}:
            return "Serveur inconnu"
        incident.clear()
        incident.update(node=node, t=time.time(), nodes_before=sum(n["ready"] for n in state["nodes"]))
        events.append({"t": time.time(), "msg": f"💥 Panne brutale : suppression de la VM {node}"})
    subprocess.Popen(
        ["gcloud", "compute", "instances", "delete", node, "--zone", ZONE, "--quiet", "--async", *(["--project", PROJECT] if PROJECT else [])],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    return "ok"


def snapshot() -> dict:
    with lock:
        inc = dict(incident)
        tot = inc.get("ok", 0) + inc.get("ko", 0)
        if inc:
            inc["availability"] = 100 * inc.get("ok", 0) / tot if tot else None
            inc["elapsed"] = time.time() - inc["t"]
        return {
            **state,
            "seconds": list(seconds)[-60:],
            "events": events[-12:],
            "incident": inc,
            "now": time.time(),
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/state"):
            self._send(200, json.dumps(snapshot()).encode(), "application/json")
        else:
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.startswith("/kill/"):
            res = kill(self.path.split("/kill/", 1)[1])
            self._send(200 if res == "ok" else 409, json.dumps({"result": res}).encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")


PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Haute disponibilité</title>
<style>
:root{--bg:#0f1420;--card:#18202f;--line:#2a3448;--fg:#e8edf6;--mut:#93a0b8;--good:#34c47c;--bad:#ef5b5b;--warn:#f0b43c;--acc:#5b8def}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:20px;margin:0}.sub{color:var(--mut)}main{padding:20px 24px;display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}.card h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);margin:0 0 12px}
.wide{grid-column:1/-1}.node{border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px}
.node.down{border-color:var(--bad);background:color-mix(in srgb,var(--bad) 12%,transparent)}
.nh{display:flex;justify-content:space-between;align-items:center;gap:8px}.nn{font-weight:600}
.pill{font-size:12px;padding:2px 8px;border-radius:99px;background:var(--line)}.ok{color:var(--good)}.ko{color:var(--bad)}
.pods{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.pod{font-size:12px;padding:3px 8px;border-radius:6px;background:color-mix(in srgb,var(--good) 22%,transparent)}
.pod.nr{background:color-mix(in srgb,var(--warn) 25%,transparent)}.pod.del{background:color-mix(in srgb,var(--bad) 25%,transparent);text-decoration:line-through}
button{background:var(--bad);color:#fff;border:0;border-radius:8px;padding:7px 12px;font-weight:600;cursor:pointer}button:disabled{opacity:.35;cursor:default}
.bars{display:flex;align-items:flex-end;gap:2px;height:120px}.bar{flex:1;display:flex;flex-direction:column-reverse;min-width:3px}
.bar i{display:block}.kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.kpi{background:var(--bg);border-radius:10px;padding:10px}
.kpi b{display:block;font-size:26px}.kpi span{color:var(--mut);font-size:13px}#ev div{padding:4px 0;border-bottom:1px solid var(--line)}#ev time{color:var(--mut);margin-right:8px}
.big{font-size:42px;font-weight:700}
</style></head><body>
<header><div><h1>🛡️ Haute disponibilité — panne d'un serveur GKE</h1><div class="sub" id="url">connexion au cluster…</div></div>
<div style="margin-left:auto;text-align:right"><div class="sub">Site vu par les clients</div><div class="big" id="live">…</div></div></header>
<main>
<section class="card"><h2>Serveurs du cluster (nœuds GKE) et leurs pods</h2><div id="nodes"></div></section>
<section class="card"><h2>Mesures de la panne</h2><div class="kpis">
<div class="kpi"><b id="k-av">—</b><span>disponibilité pendant la panne</span></div>
<div class="kpi"><b id="k-err">—</b><span>requêtes en échec</span></div>
<div class="kpi"><b id="k-pods">—</b><span>pods rétablis après (MTTR)</span></div>
<div class="kpi"><b id="k-node">—</b><span>serveur recréé par GKE après</span></div></div>
<p class="sub" style="margin-bottom:0">Sonde : 10 requêtes/s vers le site, chacune sur une nouvelle connexion (comme 10 clients différents).</p></section>
<section class="card wide"><h2>Requêtes par seconde (60 dernières s) — <span class="ok">■ réussies</span> <span class="ko">■ échouées</span></h2><div class="bars" id="bars"></div></section>
<section class="card wide"><h2>Événements</h2><div id="ev" class="sub">—</div></section>
</main>
<script>
const $=id=>document.getElementById(id), fmt=s=>s==null?"—":Math.round(s)+" s";
const tm=t=>new Date(t*1000).toLocaleTimeString("fr-FR");
async function kill(n){if(!confirm("Couper brutalement le serveur "+n+" ?"))return;
 const r=await fetch("/kill/"+n,{method:"POST"});const j=await r.json();if(j.result!=="ok")alert(j.result);}
async function tick(){let s;try{s=await (await fetch("/state")).json()}catch(e){return}
 $("url").textContent="Site : "+(s.url||"?")+"   ·   "+s.available+"/"+s.desired+" pods disponibles";
 const busy=s.incident.t&&!s.incident.done;
 $("nodes").innerHTML=s.nodes.map(n=>{const pods=s.pods.filter(p=>p.node===n.name);
  return `<div class="node ${n.ready?"":"down"}"><div class="nh"><span class="nn">🖥️ ${n.name.slice(-4)} <span class="pill ${n.ready?"ok":"ko"}">${n.ready?"en ligne":"HORS LIGNE"}</span></span>
  <button ${busy||!n.ready?"disabled":""} onclick="kill('${n.name}')">💥 Couper ce serveur</button></div>
  <div class="pods">${pods.map(p=>`<span class="pod ${p.deleting?"del":p.ready?"":"nr"}">${p.name.slice(-5)}</span>`).join("")||'<span class="sub">aucun pod</span>'}</div></div>`}).join("");
 const last=s.seconds[s.seconds.length-1];
 if(last){const up=last[2]===0;$("live").innerHTML=up?'<span class="ok">✔ EN LIGNE</span>':'<span class="ko">✘ ERREURS</span>';}
 $("bars").innerHTML=s.seconds.map(x=>`<div class="bar" title="${tm(x[0])} : ${x[1]} ok, ${x[2]} échecs"><i style="height:${x[1]*11}px;background:var(--good)"></i><i style="height:${x[2]*11}px;background:var(--bad)"></i></div>`).join("");
 const i=s.incident;
 if(i.t){$("k-av").textContent=i.availability==null?"—":i.availability.toFixed(2)+" %";
  $("k-err").textContent=(i.ko||0)+" / "+((i.ok||0)+(i.ko||0));
  $("k-pods").textContent=i.pods_ok?fmt(i.pods_ok-i.t):"en cours… "+fmt(i.elapsed);
  $("k-node").textContent=i.node_back?fmt(i.node_back-i.t):(i.node_lost?"en cours…":"—");}
 $("ev").innerHTML=s.events.slice().reverse().map(e=>`<div><time>${tm(e.t)}</time>${e.msg}</div>`).join("")||"—";}
setInterval(tick,1000);tick();
</script></body></html>"""


if __name__ == "__main__":
    threading.Thread(target=poll_cluster, daemon=True).start()
    threading.Thread(target=probe, daemon=True).start()
    event("Panneau démarré : sonde active vers le site")
    print(f"Panneau haute disponibilité : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
