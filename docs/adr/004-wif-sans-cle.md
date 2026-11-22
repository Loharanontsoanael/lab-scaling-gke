# ADR 004 — Déploiement par Workload Identity Federation

**Statut :** accepté

## Contexte
GitHub Actions doit pousser des images et déployer sur GKE. Une clé JSON de compte de service stockée
en secret GitHub est une identité longue durée : si elle fuit, elle reste valable jusqu'à sa révocation.

## Décision
Workload Identity Federation : le job demande un jeton OIDC à GitHub (`id-token: write`), GCP
l'échange contre un jeton de courte durée pour le compte `lab-github-deployer`. Le fournisseur
n'accepte que le dépôt `Loharanontsoanael/lab-scaling-gke` (`attribute_condition`). Rôles minimaux :
`artifactregistry.writer` sur ce seul dépôt d'images, `container.developer` sur le projet.

## Conséquences
- ✅ Aucun secret de longue durée ; l'accès est limité à un dépôt.
- ✅ Recommandé par Google et par `google-github-actions/auth`.
- ℹ️ Les identifiants WIF changent si l'infra est recréée : mettre à jour les secrets `WIF_PROVIDER` / `DEPLOYER_SA`.
