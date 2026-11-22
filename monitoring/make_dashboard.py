"""Génère monitoring/dashboard-configmap.yaml (tableau de bord Grafana du lab).

Usage : python3 monitoring/make_dashboard.py
"""

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
NS = 'namespace="lab"'

PANELS = [
    # (titre, requête PromQL, légende, unité, largeur)
    ("Requêtes / seconde", f"sum(rate(http_requests_total{{{NS}}}[1m]))", "req/s", "reqps", 8),
    (
        "Latence p95",
        f"histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{{{NS}}}[1m])) by (le))",
        "p95",
        "s",
        8,
    ),
    (
        "Erreurs 5xx / seconde",
        f'sum(rate(http_requests_total{{{NS},status="5xx"}}[1m]))',
        "5xx/s",
        "reqps",
        8,
    ),
    (
        "CPU par pod",
        f'sum(rate(container_cpu_usage_seconds_total{{{NS},container="app"}}[1m])) by (pod)',
        "{{pod}}",
        "short",
        12,
    ),
    (
        "RAM par pod",
        f'sum(container_memory_working_set_bytes{{{NS},container="app"}}) by (pod)',
        "{{pod}}",
        "bytes",
        12,
    ),
    ("Pods : voulus (HPA) vs disponibles", None, None, "short", 12),
    (
        "Nœuds prêts dans le cluster",
        'sum(kube_node_status_condition{condition="Ready",status="true"})',
        "nœuds prêts",
        "short",
        12,
    ),
    (
        "CPU demandé par pod (scaling vertical)",
        f'max(kube_pod_container_resource_requests{{{NS},container="app",resource="cpu"}}) by (pod)',
        "{{pod}}",
        "short",
        12,
    ),
    (
        "RAM demandée par pod (scaling vertical)",
        f'max(kube_pod_container_resource_requests{{{NS},container="app",resource="memory"}}) by (pod)',
        "{{pod}}",
        "bytes",
        12,
    ),
    (
        "Throttling CPU (% des périodes CFS bridées)",
        f'100 * sum(rate(container_cpu_cfs_throttled_periods_total{{{NS},container="app"}}[1m])) by (pod) / sum(rate(container_cpu_cfs_periods_total{{{NS},container="app"}}[1m])) by (pod)',
        "{{pod}}",
        "percent",
        12,
    ),
    (
        "Redémarrages de conteneurs (crash, OOMKill)",
        f"sum(increase(kube_pod_container_status_restarts_total{{{NS}}}[5m])) by (pod)",
        "{{pod}}",
        "short",
        12,
    ),
]


def panel(i, title, expr, legend, unit, width, x, y):
    if expr is None:  # panneau spécial à 2 courbes
        targets = [
            {
                "refId": "A",
                "datasource": DS,
                "expr": f"kube_horizontalpodautoscaler_status_desired_replicas{{{NS}}}",
                "legendFormat": "voulus (HPA)",
            },
            {
                "refId": "B",
                "datasource": DS,
                "expr": f'kube_deployment_status_replicas_available{{{NS},deployment="webapp"}}',
                "legendFormat": "disponibles",
            },
        ]
    else:
        targets = [{"refId": "A", "datasource": DS, "expr": expr, "legendFormat": legend}]
    return {
        "id": i,
        "type": "timeseries",
        "title": title,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": width, "h": 8},
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": {"lineWidth": 2, "fillOpacity": 10}},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "targets": targets,
    }


def build():
    panels, x, y = [], 0, 0
    for i, (title, expr, legend, unit, w) in enumerate(PANELS, start=1):
        if x + w > 24:
            x, y = 0, y + 8
        panels.append(panel(i, title, expr, legend, unit, w, x, y))
        x += w
    return {
        "uid": "scaling-lab",
        "title": "Scaling Lab — GKE",
        "tags": ["lab", "scaling"],
        "timezone": "browser",
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "schemaVersion": 39,
        "panels": panels,
    }


def main():
    dashboard = json.dumps(build(), indent=2, ensure_ascii=False)
    indented = "\n".join("    " + line for line in dashboard.splitlines())
    out = Path(__file__).with_name("dashboard-configmap.yaml")
    out.write_text(
        "# Généré par make_dashboard.py — ne pas éditer à la main\n"
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n"
        "  name: scaling-lab-dashboard\n  namespace: monitoring\n"
        '  labels:\n    grafana_dashboard: "1"\n'
        "data:\n  scaling-lab.json: |\n" + indented + "\n",
        encoding="utf-8",
    )
    print(f"écrit : {out}")


if __name__ == "__main__":
    main()
