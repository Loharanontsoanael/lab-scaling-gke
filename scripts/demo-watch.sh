#!/usr/bin/env bash
# Vue live pour la démo : utilisée par `make demo-watch` (watch -n2).
# Affiche les nœuds avec leurs pods, l'état du HPA et la consommation réelle.
set -uo pipefail
B=$'\e[1m'; C=$'\e[36m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'

echo "${B}⎈  Lab Scaling GKE — $(date +%T)${N}"
echo

# --- Autoscaler (HPA) ou VPA selon le mode
hpa=$(kubectl -n lab get hpa webapp -o jsonpath='{.status.currentMetrics[0].resource.current.averageUtilization}|{.spec.metrics[0].resource.target.averageUtilization}|{.status.currentReplicas}|{.status.desiredReplicas}|{.spec.minReplicas}|{.spec.maxReplicas}' 2>/dev/null)
if [[ -n "$hpa" ]]; then
  IFS='|' read -r cur tgt rep des min max <<<"$hpa"
  col=$G; [[ "${cur:-0}" -gt "${tgt:-60}" ]] && col=$R
  echo "${B}HPA${N}  CPU moyen ${col}${cur:-?}%${N} / cible ${tgt}%   pods ${C}${rep}${N} → voulus ${C}${des}${N}   (min ${min}, max ${max})"
else
  echo "${B}Mode${N} sans HPA (vertical ou fixe)"
fi
echo

# --- Nœuds et pods placés dessus
echo "${B}NŒUDS ET PODS${N}"
pods=$(kubectl -n lab get pods -l app.kubernetes.io/name=webapp --no-headers \
  -o custom-columns='N:.spec.nodeName,P:.metadata.name,R:.status.containerStatuses[0].ready,PH:.status.phase' 2>/dev/null)
kubectl get nodes --no-headers -o custom-columns='N:.metadata.name,S:.status.conditions[?(@.type=="Ready")].status,U:.spec.unschedulable' 2>/dev/null |
while read -r node ready unsched; do
  short=${node#gke-lab-cluster-main-}
  state="${G}prêt${N}"; [[ "$ready" != "True" ]] && state="${R}HS${N}"; [[ "$unsched" == "true" ]] && state="${Y}drainé${N}"
  line=$(awk -v n="$node" -v g="$G" -v y="$Y" -v z="$N" '$1==n { printf "%s", ($3=="true" ? g "●" z : y "○" z) }' <<<"$pods")
  printf "  %-22s %-16s %s\n" "$short" "$state" "$line"
done
pending=$(awk '$1=="<none>"' <<<"$pods" | wc -l)
[[ $pending -gt 0 ]] && echo "  ${Y}⏳ $pending pod(s) en attente d'un nœud → Cluster Autoscaler${N}"
echo "  ${G}●${N} pod prêt   ${Y}○${N} pod en démarrage"
echo

# --- Consommation réelle (metrics-server)
echo "${B}CONSOMMATION RÉELLE${N}"
kubectl -n lab top pods --no-headers 2>/dev/null | sort -k2 -h -r | head -12 |
  awk '{printf "  %-32s CPU %-7s RAM %s\n", $1, $2, $3}'
echo
kubectl -n lab get pods -l app.kubernetes.io/name=webapp --no-headers \
  -o custom-columns='P:.metadata.name,CPU:.spec.containers[0].resources.requests.cpu,RST:.status.containerStatuses[0].restartCount' 2>/dev/null |
  awk 'NR==1{printf "  ressources demandées par pod : CPU %s\n", $2} {r+=$3} END{printf "  redémarrages cumulés : %d\n", r}'
