resource "google_artifact_registry_repository" "images" {
  repository_id = "lab"
  location      = var.region
  format        = "DOCKER"
  description   = "Images du lab scaling"

  # Ne garder que les 10 dernières versions (évite l'accumulation et les coûts)
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-last-10"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "2592000s" # 30 jours
    }
  }

  depends_on = [google_project_service.this]
}
