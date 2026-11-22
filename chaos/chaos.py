"""Expériences de chaos : on injecte une panne pendant qu'une sonde interroge l'application,
et on mesure la disponibilité vue par le client et le temps de rétablissement (MTTR).

Pannes :
  pod   crash du processus d'un pod             → Kubernetes redémarre le conteneur
  node  drain d'un nœud (maintenance planifiée)  → pods replanifiés ailleurs, PDB respecté
  vm    suppression brutale d'une VM (panne)     → pods évincés après détection, VM recréée par GKE

Usage : python chaos/chaos.py pod|node|vm [--repeat 3] [--rate 10] [--observe 180]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments"))
import k8s  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ZONE = os.getenv("ZONE", "europe-west1-b")


def log(msg: str) -> None:
    print(f"\033[1;35m[{datetime.now():%H:%M:%S}]\033[0m {msg}", flush=True)


class Probe(threading.Thread):
    """Envoie `rate` requêtes/s, chacune sur une NOUVELLE connexion TCP
    (sinon le load balancer L4 enverrait tout vers le même pod)."""

    def __init__(self, url: str, rate: float, live: bool = True):
        super().__init__(daemon=True)
        self.url, self.period, self.live = url, 1 / rate, live
        self.samples: list[tuple[float, int, str, float]] = []
        self._halt = threading.Event()
        self._printed = 0

    def run(self):
        sec = int(time.time())
        while not self._halt.is_set():
            t = time.time()
            try:
                r = requests.get(self.url, timeout=2, headers={"Connection": "close"})
                pod = r.json().get("pod", "?") if r.ok else "-"
                self.samples.append((t, r.status_code, pod, time.time() - t))
            except requests.RequestException:
                self.samples.append((t, 0, "-", time.time() - t))
            if self.live and int(time.time()) != sec:
                sec = int(time.time())
                self._print_second()
            time.sleep(max(0, self.period - (time.time() - t)))

    def _print_second(self):
        """Une ligne par seconde pour la démo : réussites, échecs, pods qui ont répondu."""
        new = self.samples[self._printed :]
        self._printed = len(self.samples)
        ok = sum(s[1] == 200 for s in new)
        ko = len(new) - ok
        pods = sorted({s[2][-5:] for s in new if s[1] == 200})
        bar = "\033[32m" + "█" * ok + "\033[31m" + "█" * ko + "\033[0m"
        status = "\033[32m✔\033[0m" if ko == 0 else "\033[31m✘\033[0m"
        print(f"  {datetime.now():%H:%M:%S} {status} {bar:<40} {ok:>2} ok {ko:>2} échecs   pods : {', '.join(pods)}", flush=True)

    def stop(self):
        self._halt.set()
        self.join()


def monitoring_nodes() -> set[str]:
    """Nœuds qui hébergent Prometheus ou Grafana (hors DaemonSets) : à épargner pendant la démo,
    sinon on perd l'historique des métriques et le port-forward de Grafana."""
    try:
        pods = k8s.kubectl_json("-n", "monitoring", "get", "pods")["items"]
    except Exception:
        return set()
    keep = ("prometheus-monitoring", "monitoring-grafana")
    return {p["spec"].get("nodeName") for p in pods if p["metadata"]["name"].startswith(keep)}


def victim_pod(spare_monitoring: bool = False) -> tuple[str, str]:
    pods = k8s.kubectl_json("-n", k8s.NS, "get", "pods", "-l", "app.kubernetes.io/name=webapp")["items"]
    running = [p for p in pods if p["status"].get("phase") == "Running"]
    if spare_monitoring:
        avoid = monitoring_nodes()
        safe = [p for p in running if p["spec"]["nodeName"] not in avoid]
        if safe:
            running = safe
        else:
            log("⚠️ Tous les nœuds avec un pod de l'app hébergent aussi le monitoring : Grafana va être perturbé")
    p = running[0]
    return p["metadata"]["name"], p["spec"]["nodeName"]


def ready_nodes() -> int:
    nodes = k8s.kubectl_json("get", "nodes")["items"]
    return sum(
        1
        for n in nodes
        if not n["spec"].get("unschedulable")
        and any(c["type"] == "Ready" and c["status"] == "True" for c in n["status"]["conditions"])
    )


def inject(mode: str) -> dict:
    pod, node = victim_pod(spare_monitoring=mode in ("node", "vm"))
    info = {"pod": pod, "node": node}
    if mode == "pod":
        log(f"💥 Crash du processus dans {pod}")
        k8s.kubectl(
            "-n", k8s.NS, "exec", pod, "--", "python", "-c",
            "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/crash')",
            check=False,
        )  # fmt: skip
    elif mode == "node":
        log(f"💥 Drain du nœud {node} (maintenance)")
        k8s.kubectl("drain", node, "--ignore-daemonsets", "--delete-emptydir-data", "--timeout=180s", check=False)
    elif mode == "vm":
        log(f"💥 Suppression brutale de la VM {node}")
        k8s.sh("gcloud", "compute", "instances", "delete", node, "--zone", ZONE, "--quiet", "--async")
    return info


def wait_recovery(mode: str, t_inject: float, nodes_before: int, timeout: int = 900) -> dict:
    """Rétabli = tous les pods désirés sont prêts à nouveau (après avoir constaté la perte)."""
    t_pods = t_nodes = None
    saw_degraded = mode == "node"  # le drain est synchrone : la perte a déjà eu lieu
    while time.time() - t_inject < timeout:
        desired, available = k8s.deployment_status()
        restarts = k8s.kubectl("-n", k8s.NS, "get", "pods", "-o", "jsonpath={.items[*].status.containerStatuses[0].restartCount}")
        if available < desired or (mode == "pod" and any(int(x) > 0 for x in restarts.split())):
            saw_degraded = True
        if t_pods is None and saw_degraded and available >= desired:
            t_pods = time.time()
            log(f"✅ Pods rétablis après {t_pods - t_inject:.0f} s ({available}/{desired})")
        if mode == "vm" and t_nodes is None and t_pods and ready_nodes() >= nodes_before:
            t_nodes = time.time()
            log(f"✅ VM recréée et nœud prêt après {t_nodes - t_inject:.0f} s")
        if t_pods and (mode != "vm" or t_nodes):
            break
        time.sleep(2)
    return {
        "mttr_pods_s": round(t_pods - t_inject, 1) if t_pods else None,
        "mttr_node_s": round(t_nodes - t_inject, 1) if t_nodes else None,
    }


def summarize(samples, t_inject: float) -> dict:
    ok = [s for s in samples if s[1] == 200]
    fails = [s for s in samples if s[1] != 200]
    after = [s for s in samples if s[0] >= t_inject]
    return {
        "requests": len(samples),
        "failures": len(fails),
        "availability_pct": 100 * len(ok) / len(samples) if samples else None,
        "availability_after_injection_pct": 100 * sum(s[1] == 200 for s in after) / len(after) if after else None,
        "error_window_s": (fails[-1][0] - fails[0][0]) if len(fails) > 1 else 0.0,
        "first_error_after_injection_s": (fails[0][0] - t_inject) if fails else None,
        "p99_latency_ms": 1000 * statistics.quantiles([s[3] for s in ok], n=100)[98] if len(ok) > 100 else None,
        "pods_serving": sorted({s[2] for s in ok}),
    }


def run_once(mode: str, rep: int, args, out_dir: Path) -> dict:
    log(f"━━ Chaos « {mode} » — répétition {rep}/{args.repeat}")
    k8s.wait_rollout()
    url = f"http://{k8s.service_ip()}/"
    nodes_before = ready_nodes()

    probe = Probe(url, args.rate)
    probe.start()
    time.sleep(args.baseline)
    t_inject = time.time()
    info = inject(mode)
    rec = wait_recovery(mode, t_inject, nodes_before)
    time.sleep(args.observe)
    probe.stop()

    if mode == "node":
        k8s.kubectl("uncordon", info["node"], check=False)

    with open(out_dir / f"{mode}_rep{rep}_probe.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_rel_s", "status", "pod", "latency_s"])
        w.writerows((round(t - t_inject, 3), c, p, round(lat, 4)) for t, c, p, lat in probe.samples)
    res = {"mode": mode, "rep": rep, **info, **rec, **summarize(probe.samples, t_inject)}
    (out_dir / f"{mode}_rep{rep}.json").write_text(json.dumps(res, indent=2))
    log(
        f"Disponibilité {res['availability_pct']:.2f} % · échecs {res['failures']}/{res['requests']} · "
        f"fenêtre d'erreurs {res['error_window_s']:.1f} s · MTTR pods {res['mttr_pods_s']} s"
    )
    return res


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["pod", "node", "vm"])
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--rate", type=float, default=10, help="requêtes/s de la sonde")
    p.add_argument("--baseline", type=int, default=15, help="secondes de mesure avant la panne")
    p.add_argument("--observe", type=int, default=30, help="secondes d'observation après rétablissement")
    args = p.parse_args()

    out_dir = ROOT / "results" / "chaos" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    results = [run_once(args.mode, r, args, out_dir) for r in range(1, args.repeat + 1)]

    def mean(key):
        vals = [r[key] for r in results if r[key] is not None]
        return f"{statistics.mean(vals):.2f}" if vals else "n/a"

    md = [
        f"# Chaos « {args.mode} » — {len(results)} répétitions",
        "",
        "| Rép. | Disponibilité % | Échecs | Fenêtre d'erreurs (s) | MTTR pods (s) | MTTR nœud (s) |",
        "|---|---|---|---|---|---|",
        *[
            f"| {r['rep']} | {r['availability_pct']:.2f} | {r['failures']}/{r['requests']} | {r['error_window_s']:.1f} "
            f"| {r['mttr_pods_s'] or 'n/a'} | {r['mttr_node_s'] or 'n/a'} |"
            for r in results
        ],
        f"| **moy.** | **{mean('availability_pct')}** | | {mean('error_window_s')} "
        f"| {mean('mttr_pods_s')} | {mean('mttr_node_s')} |",
    ]
    (out_dir / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())
