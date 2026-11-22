# Architecture Decision Records

Chaque ADR documente une décision : contexte, décision, conséquences (format de Michael Nygard).

| # | Décision |
|---|---|
| [001](001-gke-standard.md) | GKE Standard zonal plutôt qu'Autopilot ou Cloud Run |
| [002](002-hpa-cpu-vpa-separes.md) | HPA sur le CPU uniquement ; VPA jamais actif en même temps |
| [003](003-terraform-kustomize.md) | Terraform pour l'infra, Kustomize pour les manifestes |
| [004](004-wif-sans-cle.md) | CI/CD par Workload Identity Federation, sans clé de service |
| [005](005-protocole-experimental.md) | Protocole expérimental : générateur externe, paliers, répétitions entrelacées |
