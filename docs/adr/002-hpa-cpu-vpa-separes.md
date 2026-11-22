# ADR 002 — HPA sur le CPU, VPA séparé

**Statut :** accepté

## Contexte
HPA et VPA sont deux boucles de contrôle. Si les deux agissent sur le CPU, le VPA augmente les
`requests`, ce qui fait baisser l'utilisation en % vue par le HPA, qui retire des pods, ce qui
remonte l'utilisation… La documentation GKE déconseille explicitement de les combiner sur les mêmes métriques.

## Décision
- Overlay `horizontal` : HPA sur le **CPU seul** (cible 60 %, 3 → 20 pods), plus un VPA en mode
  **`Off`** qui ne fait que **recommander** (bonne pratique : observer avant d'automatiser).
- Overlay `vertical` : VPA en mode **`InPlaceOrRecreate`** (GKE ≥ 1.34), pas de HPA.
- `kubectl apply --prune` supprime l'autoscaler de l'ancien mode au changement d'overlay.

## Conséquences
- ✅ Aucune oscillation entre contrôleurs ; chaque expérience isole un seul mécanisme.
- ✅ Le VPA `Off` fournit des données de dimensionnement pour le rapport, même pendant E2.
- ℹ️ Cible à 60 % : marge pour absorber un pic pendant les ~30–60 s nécessaires à l'ajout de pods.
- ℹ️ Le scale-down est volontairement lent (fenêtre de 120 s) pour éviter le *flapping*.
