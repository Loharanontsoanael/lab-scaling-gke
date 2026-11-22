# Plan du rapport — Scalabilité et haute disponibilité d'une application conteneurisée sur GKE

*(≈ 25–35 pages ; les figures et tableaux viennent de `results/*/summary.md`)*

## 1. Introduction
- Contexte : élasticité du cloud, applications web soumises à des charges variables
- Problématique : *comment une architecture en cluster conteneurisé absorbe-t-elle la charge et les
  pannes, et à quel coût ? Quand préférer le scaling vertical ou le scaling horizontal ?*
- Contributions : plateforme reproductible (IaC), protocole expérimental, résultats chiffrés

## 2. État de l'art
- 2.1 Grid vs cluster vs cloud : Foster & Kesselman, la checklist de Foster (2002), organisations
  virtuelles, WLCG/HTCondor ; clusters HPC (Slurm) ; élasticité du cloud
- 2.2 Orchestration de conteneurs : de Borg à Kubernetes (Verma et al., EuroSys 2015 ; Burns et al., 2016), boucles de réconciliation
- 2.3 Autoscaling : HPA (algorithme, tolérance, stabilisation), VPA (recommender/updater/admission, in-place resize), Cluster Autoscaler
- 2.4 Modèles de performance : loi de Little, files M/M/c, loi d'Amdahl, **Universal Scalability Law** (Gunther)
- 2.5 Haute disponibilité : redondance, domaines de défaillance, SLI/SLO, MTTD/MTTR, chaos engineering (Basiri et al., IEEE Software 2016)

## 3. Architecture et mise en œuvre
- 3.1 Application de test (endpoints CPU/mémoire, métriques, arrêt propre)
- 3.2 Infrastructure GCP en Terraform (schéma, réseau privé, IAM moindre privilège)
- 3.3 Déploiement Kubernetes (Kustomize, sondes, PDB, topology spread, NetworkPolicy, Pod Security)
- 3.4 Observabilité (Prometheus, Grafana, métriques retenues)
- 3.5 CI/CD et sécurité de la chaîne logicielle (WIF, Trivy, Dependabot)
- 3.6 Décisions d'architecture (résumé des ADR)

## 4. Méthodologie expérimentale
- Générateur de charge externe, modèle fermé, paliers, régime transitoire écarté
- Métriques côté client et côté cluster ; 3 répétitions entrelacées, IC 95 %
- Menaces à la validité : voisins bruyants (VM e2 partagées), cluster zonal, taille d'échantillon,
  GIL Python, un seul type de machine

## 5. Résultats
- **E1 Saturation d'un pod** : courbe débit/latence, point de saturation, vérification de N = X(R+Z), effet du throttling CFS
- **E2 Scaling horizontal automatique** : chronogramme charge/pods/nœuds/latence ; délai de décision et
  délai jusqu'aux pods prêts ; surcoût quand il faut un nouveau nœud ; scale-down
- **E3 Scalabilité** : débit max en fonction de N, ajustement USL (α, β, N*), interprétation
- **E4 Vertical vs horizontal à budget égal** : 1×1600m vs 2×800m vs 4×400m ; limite du vertical (GIL)
- **E5 Scaling vertical automatique (VPA)** : recommandations, délai d'application, perturbation (in-place vs recreate)
- **E6 Résilience** : pod / drain / VM → disponibilité %, fenêtre d'erreurs, MTTR

## 6. Discussion
- Vertical vs horizontal : performance, coût par requête, disponibilité, limites
- Réactivité de l'autoscaling vs pics brusques (sur-provisionnement, scaling prédictif, KEDA)
- Coût mesuré de la plateforme
- Perspectives : cluster régional multi-zone, multi-cluster et fédération (le retour du grid),
  service mesh, autoscaling sur métriques métier

## 7. Conclusion

## Annexes
- Reproduire les expériences (commandes `make`) ; tableaux complets ; extraits de configuration
