variable "project_id" {
  description = "ID du projet GCP"
  type        = string
}

variable "region" {
  description = "Région GCP"
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "Zone du cluster (cluster zonal : 1 seul plan de contrôle gratuit)"
  type        = string
  default     = "europe-west1-b"
}

variable "cluster_name" {
  type    = string
  default = "lab-cluster"
}

variable "machine_type" {
  description = "Type de VM des nœuds GKE"
  type        = string
  default     = "e2-standard-2"
}

variable "node_count" {
  description = "Nombre initial de nœuds"
  type        = number
  default     = 3
}

variable "min_nodes" {
  type    = number
  default = 2
}

variable "max_nodes" {
  type    = number
  default = 5
}

variable "github_repository" {
  description = "Dépôt GitHub autorisé à déployer via Workload Identity Federation (owner/repo)"
  type        = string
  default     = "Loharanontsoanael/lab-scaling-gke"
}

variable "enable_loadgen" {
  description = "Créer la VM génératrice de charge (Locust) pour les expériences (inutile pour la démo)"
  type        = bool
  default     = false
}

variable "enable_github_wif" {
  description = <<-EOT
    Créer la fédération d'identité pour le déploiement GitHub Actions.
    Attention : un pool WIF supprimé reste réservé 30 jours → changer wif_pool_id pour le recréer avant.
  EOT
  type        = bool
  default     = false
}

variable "wif_pool_id" {
  type    = string
  default = "github"
}

variable "billing_account" {
  description = "ID du compte de facturation pour l'alerte budget (vide = pas d'alerte)"
  type        = string
  default     = ""
}

variable "budget_amount_eur" {
  description = "Montant mensuel de l'alerte budget, dans la devise du compte de facturation"
  type        = number
  default     = 20
}
