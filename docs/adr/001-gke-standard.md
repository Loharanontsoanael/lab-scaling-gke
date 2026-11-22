# ADR 001 — GKE Standard zonal

**Statut :** accepté

## Contexte
Le projet doit montrer des serveurs qui tombent, des nœuds ajoutés par l'autoscaler et l'effet du
redimensionnement des machines. Options : Cloud Run, GKE Autopilot, GKE Standard, VMs + MIG.

## Décision
GKE **Standard**, **zonal** (`europe-west1-b`), un node pool `e2-standard-2` géré à part
(2 à 5 nœuds, auto-repair), nœuds privés derrière Cloud NAT.

## Conséquences
- ✅ Les nœuds sont visibles et manipulables (`drain`, suppression de VM) : indispensable pour les expériences de panne.
- ✅ Contrôle total sur le type de machine, le Cluster Autoscaler et le VPA.
- ✅ Le plan de contrôle d'un cluster zonal est couvert par le crédit gratuit GKE.
- ❌ Autopilot est la recommandation par défaut de Google (moins d'exploitation, facturation au pod), mais il masque les nœuds.
- ❌ Zonal = un seul domaine de défaillance : la perte de la zone entière n'est pas couverte. Un cluster
  **régional** (3 zones) est la cible en production ; c'est discuté comme perspective dans le rapport.
