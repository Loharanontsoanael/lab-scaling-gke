"""Orchestrateur d'expériences reproductibles.

Pour chaque configuration × répétition :
  1. prépare le cluster (overlay Kustomize, nombre de pods, ressources) ;
  2. attend un état stable ;
  3. lance Locust en paliers (sur la VM loadgen ou en local) ;
  4. observe le retour au calme (scale-down) ;
  5. enregistre les métriques Prometheus + les CSV Locust + les métadonnées.

Exemples :
  python experiments/run_experiment.py --list
  python experiments/run_experiment.py e2-horizontal-hpa --repeat 3
  python experiments/run_experiment.py e1-saturation --runner local --repeat 1
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import k8s
from scenarios import SCENARIOS, PodConfig, Scenario

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
ZONE = os.getenv("ZONE", "europe-west1-b")
LOADGEN = os.getenv("LOADGEN_VM", "lab-loadgen")

NSF = 'namespace="lab"'
METRICS = {
    "replicas_desired": f"sum(kube_horizontalpodautoscaler_status_desired_replicas{{{NSF}}})"
    f' or sum(kube_deployment_spec_replicas{{{NSF},deployment="webapp"}})',
    "replicas_available": f'sum(kube_deployment_status_replicas_available{{{NSF},deployment="webapp"}})',
    "nodes_ready": 'sum(kube_node_status_condition{condition="Ready",status="true"})',
    "cpu_cores": f'sum(rate(container_cpu_usage_seconds_total{{{NSF},container="app"}}[30s]))',
    "cpu_request_per_pod": f'avg(kube_pod_container_resource_requests{{{NSF},container="app",resource="cpu"}})',
    "memory_bytes": f'sum(container_memory_working_set_bytes{{{NSF},container="app"}})',
    "throttled_pct": f'100 * sum(rate(container_cpu_cfs_throttled_periods_total{{{NSF},container="app"}}[30s]))'
    f' / sum(rate(container_cpu_cfs_periods_total{{{NSF},container="app"}}[30s]))',
    "server_rps": f"sum(rate(http_requests_total{{{NSF}}}[30s]))",
    "server_p95_s": f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{{NSF}}}[30s])) by (le))",
    "restarts": f"sum(kube_pod_container_status_restarts_total{{{NSF}}})",
}


def log(msg: str) -> None:
    print(f"\033[1;36m[{datetime.now():%H:%M:%S}]\033[0m {msg}", flush=True)


# --------------------------------------------------------------------------- préparation
def prepare(scenario: Scenario, cfg: PodConfig) -> None:
    log(f"Déploiement overlay « {scenario.overlay} »")
    subprocess.run(["make", "-s", "-C", str(ROOT), "deploy", f"OVERLAY={scenario.overlay}"], check=True)
    if scenario.overlay == "fixed":
        if cfg.cpu:
            k8s.kubectl(
                "-n", k8s.NS, "set", "resources", "deployment/webapp", "-c", "app",
                f"--requests=cpu={cfg.cpu},memory={cfg.memory}",
                f"--limits=cpu={cfg.cpu},memory={cfg.memory}",
            )  # fmt: skip
        k8s.kubectl("-n", k8s.NS, "scale", "deployment/webapp", f"--replicas={cfg.replicas}")
    k8s.wait_rollout()
    wait_stable(cfg.replicas if scenario.overlay != "horizontal" else None)


def wait_stable(expected: int | None, timeout: int = 600) -> None:
    """Attend que tous les pods soient prêts (et, pour le HPA, revenus au minimum)."""
    start = time.time()
    while time.time() - start < timeout:
        desired, available = k8s.deployment_status()
        target = expected if expected is not None else 3
        if desired == target and available == target:
            log(f"État stable : {available} pods prêts")
            time.sleep(20)  # laisse retomber le CPU de démarrage
            return
        log(f"Attente stabilisation : {available}/{desired} prêts (cible {target})")
        time.sleep(15)
    log("⚠️ Pas stable après le délai, on continue quand même")


# --------------------------------------------------------------------------- charge
def locust_env(scenario: Scenario) -> dict[str, str]:
    return {
        "STEPS": scenario.steps,
        "STEP_SECONDS": str(scenario.step_seconds),
        "LOAD_PROFILE": scenario.profile,
    }


def run_locust_local(scenario: Scenario, host: str, out: Path) -> None:
    locust = ROOT / ".venv" / "bin" / "locust"
    cmd = [
        str(locust), "-f", str(ROOT / "loadtest" / "locustfile.py"), "--host", host,
        "--headless", "--csv", str(out / "locust"), "--csv-full-history",
        "--html", str(out / "locust.html"), "--only-summary",
    ]  # fmt: skip
    with open(out / "locust.log", "w") as fh:
        subprocess.run(cmd, check=False, env={**os.environ, **locust_env(scenario)}, stdout=fh, stderr=fh)


def run_locust_loadgen(scenario: Scenario, host: str, out: Path) -> None:
    gssh = ["gcloud", "compute", "ssh", LOADGEN, "--zone", ZONE, "--tunnel-through-iap", "--quiet"]
    gscp = ["gcloud", "compute", "scp", "--zone", ZONE, "--tunnel-through-iap", "--quiet"]
    subprocess.run([*gscp, str(ROOT / "loadtest" / "locustfile.py"), f"{LOADGEN}:~/locustfile.py"], check=True)
    env = " ".join(f"{k}={v}" for k, v in locust_env(scenario).items())
    remote = (
        f"rm -rf ~/run && mkdir ~/run && {env} locust -f ~/locustfile.py --host {host} --headless "
        "--csv ~/run/locust --csv-full-history --html ~/run/locust.html --only-summary > ~/run/locust.log 2>&1; true"
    )
    subprocess.run([*gssh, "--command", remote], check=True)
    subprocess.run([*gscp, "--recurse", f"{LOADGEN}:~/run/*", str(out)], check=True)


# --------------------------------------------------------------------------- collecte
def collect(prom_url: str, start: float, end: float, out: Path) -> None:
    series = {name: dict(k8s.query_range(prom_url, expr, start, end)) for name, expr in METRICS.items()}
    timestamps = sorted({t for s in series.values() for t in s})
    with open(out / "prometheus.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", *METRICS])
        for t in timestamps:
            w.writerow([int(t), *(series[m].get(t, "") for m in METRICS)])


def run_one(scenario: Scenario, cfg: PodConfig, rep: int, args, exp_dir: Path, prom_url: str) -> None:
    out = exp_dir / f"{cfg.label.replace(' ', '_').replace('×', 'x')}__rep{rep}"
    out.mkdir(parents=True, exist_ok=True)
    log(f"━━ {scenario.key} | {cfg.label} | répétition {rep}/{args.repeat}")

    prepare(scenario, cfg)
    host = f"http://{k8s.service_ip()}"
    restarts_before = k8s.kubectl(
        "-n", k8s.NS, "get", "pods", "-o", "jsonpath={.items[*].status.containerStatuses[0].restartCount}"
    )

    t_start = time.time()
    log(f"Charge {scenario.steps} utilisateurs × {scenario.step_seconds}s sur {host} ({args.runner})")
    (run_locust_local if args.runner == "local" else run_locust_loadgen)(scenario, host, out)
    t_load_end = time.time()

    log(f"Observation du retour au calme ({scenario.cooldown_seconds}s)")
    time.sleep(scenario.cooldown_seconds)
    t_end = time.time()

    collect(prom_url, t_start - 60, t_end, out)
    meta = {
        "scenario": scenario.key,
        "title": scenario.title,
        "question": scenario.question,
        "config": cfg.__dict__,
        "repetition": rep,
        "overlay": scenario.overlay,
        "steps": scenario.steps,
        "step_seconds": scenario.step_seconds,
        "profile": scenario.profile,
        "runner": args.runner,
        "host": host,
        "t_start": t_start,
        "t_load_end": t_load_end,
        "t_end": t_end,
        "git_sha": k8s.git_sha(),
        "restarts_before": restarts_before,
        "cluster": k8s.cluster_info(),
        "utc": datetime.now(UTC).isoformat(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log(f"✅ Résultats : {out.relative_to(ROOT)}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenario", nargs="?", choices=list(SCENARIOS))
    p.add_argument("--repeat", type=int, default=3, help="répétitions par configuration (défaut 3)")
    p.add_argument("--runner", choices=["loadgen", "local"], default="loadgen")
    p.add_argument("--list", action="store_true", help="lister les scénarios")
    args = p.parse_args()

    if args.list or not args.scenario:
        for s in SCENARIOS.values():
            print(f"{s.key:28} {s.title}\n{'':28} ↳ {s.question}")
        return 0

    scenario = SCENARIOS[args.scenario]
    exp_dir = RESULTS / scenario.key / datetime.now().strftime("%Y%m%d-%H%M%S")
    with k8s.prometheus() as prom_url:
        # Répétitions entrelacées (A,B,C, A,B,C…) plutôt que groupées (A,A,A, B,B,B) :
        # limite le biais temporel (voisins bruyants, variations du réseau GCP).
        for rep in range(1, args.repeat + 1):
            for cfg in scenario.configs:
                run_one(scenario, cfg, rep, args, exp_dir, prom_url)
    log(f"Terminé. Analyse : python experiments/analyze.py {exp_dir.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
