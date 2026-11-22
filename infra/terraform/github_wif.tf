# CI/CD sans clé : GitHub Actions échange son jeton OIDC contre un jeton GCP
# (Workload Identity Federation). Seul le dépôt var.github_repository est accepté.
resource "google_iam_workload_identity_pool" "github" {
  count                     = var.enable_github_wif ? 1 : 0
  workload_identity_pool_id = var.wif_pool_id
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.enable_github_wif ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == '${var.github_repository}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  count        = var.enable_github_wif ? 1 : 0
  account_id   = "lab-github-deployer"
  display_name = "Déploiement GitHub Actions"
}

resource "google_service_account_iam_member" "deployer_wif" {
  count              = var.enable_github_wif ? 1 : 0
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

# Droits minimaux : pousser des images dans CE dépôt + déployer dans le cluster
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  count      = var.enable_github_wif ? 1 : 0
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer[0].email}"
}

resource "google_project_iam_member" "deployer_gke" {
  count   = var.enable_github_wif ? 1 : 0
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deployer[0].email}"
}
