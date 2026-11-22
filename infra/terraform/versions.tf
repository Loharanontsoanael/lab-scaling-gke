terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.13"
    }
  }

  # État distant partagé et verrouillé dans un bucket GCS.
  # Le nom du bucket est passé à l'init : make tf-init (voir Makefile).
  backend "gcs" {
    prefix = "lab-scaling-gke"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  # Nécessaire pour l'API Budgets avec des identifiants utilisateur (ADC)
  user_project_override = true
  billing_project       = var.project_id

  default_labels = {
    project    = "lab-scaling-gke"
    managed-by = "terraform"
  }
}
