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
import shutil
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


GCLOUD = shutil.which("gcloud") or str(Path.home() / ".local/google-cloud-sdk/bin/gcloud")


def delete_vm(node: str) -> None:
    """Supprime la VM ; en cas d'échec, l'erreur s'affiche dans le journal et la panne est annulée."""
    cmd = [GCLOUD, "compute", "instances", "delete", node, "--zone", ZONE, "--quiet"]
    if PROJECT:
        cmd += ["--project", PROJECT]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        ok, err = out.returncode == 0, (out.stderr or out.stdout).strip().splitlines()
    except Exception as exc:  # noqa: BLE001
        ok, err = False, [str(exc)]
    with lock:
        if ok:
            events.append({"t": time.time(), "msg": "☁️ Google Cloud a terminé la suppression de la VM"})
        else:
            events.append({"t": time.time(), "msg": "❌ Échec de la coupure : " + next((e for e in err if "ERROR" in e), err[-1] if err else "erreur inconnue")})
            incident.clear()
    print("gcloud:", ok, err, flush=True)


def kill(node: str) -> str:
    with lock:
        if incident and not incident.get("done"):
            return "Une panne est déjà en cours"
        if node not in {n["name"] for n in state["nodes"]}:
            return "Serveur inconnu"
        incident.clear()
        incident.update(node=node, t=time.time(), nodes_before=sum(n["ready"] for n in state["nodes"]))
        events.append({"t": time.time(), "msg": f"💥 Panne brutale : suppression de la VM {node}"})
    threading.Thread(target=delete_vm, args=(node,), daemon=True).start()
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
            "seconds": list(seconds)[-90:],
            "events": events[-14:],
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
            self._send(200, PAGE_FILE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path.startswith("/kill/"):
            res = kill(self.path.split("/kill/", 1)[1])
            self._send(200 if res == "ok" else 409, json.dumps({"result": res}).encode(), "application/json")
        else:
            self._send(404, b"{}", "application/json")


PAGE_FILE = Path(__file__).with_name("ha-panel.html")


if __name__ == "__main__":
    threading.Thread(target=poll_cluster, daemon=True).start()
    threading.Thread(target=probe, daemon=True).start()
    event("Panneau démarré : sonde active vers le site")
    print(f"Panneau haute disponibilité : http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
