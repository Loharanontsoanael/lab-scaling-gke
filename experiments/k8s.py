"""Petits utilitaires partagés : kubectl, Prometheus, git."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from contextlib import contextmanager

import requests

NS = "lab"
PROM_SVC = "svc/monitoring-kube-prometheus-prometheus"


def sh(*cmd: str, check: bool = True, capture: bool = True) -> str:
    res = subprocess.run(cmd, check=check, text=True, capture_output=capture)
    return res.stdout.strip() if capture else ""


def kubectl(*args: str, check: bool = True) -> str:
    return sh("kubectl", *args, check=check)


def kubectl_json(*args: str) -> dict:
    return json.loads(kubectl(*args, "-o", "json"))


def service_ip(name: str = "webapp") -> str:
    for _ in range(60):
        ip = kubectl("-n", NS, "get", "svc", name, "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}")
        if ip:
            return ip
        time.sleep(5)
    raise RuntimeError("Le load balancer n'a pas d'IP publique")


def wait_rollout(timeout: str = "300s") -> None:
    kubectl("-n", NS, "rollout", "status", "deployment/webapp", f"--timeout={timeout}")


def deployment_status() -> tuple[int, int]:
    """(réplicas désirés, réplicas disponibles)."""
    d = kubectl_json("-n", NS, "get", "deployment", "webapp")
    return d["spec"].get("replicas", 0), d["status"].get("availableReplicas", 0)


def cluster_info() -> dict:
    nodes = kubectl_json("get", "nodes")["items"]
    return {
        "nodes": len(nodes),
        "machine_types": sorted({n["metadata"]["labels"].get("node.kubernetes.io/instance-type", "?") for n in nodes}),
        "kubelet_version": nodes[0]["status"]["nodeInfo"]["kubeletVersion"] if nodes else "?",
    }


def git_sha() -> str:
    return sh("git", "rev-parse", "--short", "HEAD", check=False) or "unknown"


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@contextmanager
def prometheus(port: int = 9090):
    """Ouvre un port-forward vers Prometheus le temps de l'expérience (si pas déjà ouvert)."""
    proc = None
    if not _port_open(port):
        proc = subprocess.Popen(
            ["kubectl", "-n", "monitoring", "port-forward", PROM_SVC, f"{port}:9090"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(30):
            if _port_open(port):
                break
            time.sleep(1)
        else:
            proc.terminate()
            raise RuntimeError("Impossible de joindre Prometheus (make monitoring fait ?)")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        if proc:
            proc.terminate()


def query_range(prom_url: str, expr: str, start: float, end: float, step: int = 5) -> list[tuple[float, float]]:
    r = requests.get(
        f"{prom_url}/api/v1/query_range",
        params={"query": expr, "start": start, "end": end, "step": step},
        timeout=30,
    )
    r.raise_for_status()
    result = r.json()["data"]["result"]
    if not result:
        return []
    return [(float(t), float(v)) for t, v in result[0]["values"]]
