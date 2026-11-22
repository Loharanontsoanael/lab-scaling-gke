# ADR 003 — Terraform et Kustomize

**Statut :** accepté

## Contexte
Le cluster est créé puis détruit à chaque session (coût). Il doit être recréé à l'identique, et les
mêmes manifestes doivent servir à plusieurs modes de scaling.

## Décision
- **Terraform** pour toutes les ressources GCP, avec un état distant dans un bucket GCS versionné.
- **Kustomize** (intégré à `kubectl`) : une `base` commune et des `overlays` par mode de scaling ;
  le tag d'image est injecté au déploiement (pas de `sed`, pas de `:latest`).
- **Helm** seulement pour le logiciel tiers (kube-prometheus-stack).

## Conséquences
- ✅ Infra reproductible, revue en PR (`terraform plan`), détruite en une commande.
- ✅ Différences entre modes explicites et minimales (diff d'overlay).
- ❌ Un outil de plus à maîtriser ; le bucket d'état est créé par `make tf-bootstrap` (amorçage).
