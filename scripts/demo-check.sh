#!/usr/bin/env bash
# Vérification avant le jury : tout doit être vert.
set -uo pipefail
ok=0; ko=0
check() {  # check "description" commande...
  local desc=$1; shift
  if out=$("$@" 2>&1); then echo -e "  \e[32m✔\e[0m $desc ${out:+— $out}"; ok=$((ok+1))
  else echo -e "  \e[31m✘\e[0m $desc — ${out:0:150}"; ko=$((ko+1)); fi
}

echo -e "\e[1mVérification de la plateforme\e[0m"
check "kubectl connecté au cluster" kubectl get nodes -o name --request-timeout=10s
check "3 nœuds prêts (ou plus)" bash -c '[[ $(kubectl get nodes --no-headers | grep -cw Ready) -ge 3 ]] && kubectl get nodes --no-headers | grep -cw Ready'
check "pods de l'application prêts" bash -c 'kubectl -n lab get deploy webapp -o jsonpath="{.status.readyReplicas}/{.spec.replicas}" | grep -E "^([0-9]+)/\1$"'
check "HPA actif et métriques CPU disponibles" bash -c 'v=$(kubectl -n lab get hpa webapp -o jsonpath="{.status.currentMetrics[0].resource.current.averageUtilization}"); [[ -n $v ]] && echo "CPU $v%"'
IP=$(kubectl -n lab get svc webapp -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)
check "IP publique du load balancer" bash -c "[[ -n '$IP' ]] && echo http://$IP"
check "l'application répond" curl -fsS --max-time 5 "http://$IP/"
spread() {
  local n
  n=$(for _ in $(seq 12); do curl -s --max-time 3 -H 'Connection: close' "http://$IP/" | python3 -c 'import json,sys; print(json.load(sys.stdin)["pod"])'; done | sort -u | wc -l)
  [[ $n -ge 2 ]] && echo "$n pods différents ont répondu"
}
check "répartition de charge sur plusieurs pods" spread
check "Prometheus et Grafana prêts" bash -c '[[ $(kubectl -n monitoring get pods --no-headers | grep -vc Running) -eq 0 ]] && echo "$(kubectl -n monitoring get pods --no-headers | wc -l) pods Running"'
check "Prometheus collecte l'application" bash -c 'kubectl -n monitoring get servicemonitor -A 2>/dev/null | grep -q webapp || kubectl -n lab get servicemonitor webapp -o name'
check "dashboard Grafana installé" kubectl -n monitoring get configmap scaling-lab-dashboard -o name
check "Locust installé" bash -c "[[ -x .venv/bin/locust ]] && .venv/bin/locust --version"

echo
if [[ $ko -eq 0 ]]; then echo -e "\e[1;32m✅ Tout est prêt ($ok/$ok). Bonne chance pour le jury !\e[0m"
else echo -e "\e[1;31m⚠️  $ko problème(s) — voir docs/DEMO.md, section Dépannage\e[0m"; exit 1; fi
