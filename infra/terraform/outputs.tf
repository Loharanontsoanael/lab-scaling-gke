output "get_credentials" {
  description = "Commande pour connecter kubectl"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --zone ${var.zone} --project ${var.project_id}"
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "wif_provider" {
  description = "À mettre dans le secret GitHub WIF_PROVIDER"
  value       = var.enable_github_wif ? google_iam_workload_identity_pool_provider.github[0].name : ""
}

output "deployer_service_account" {
  description = "À mettre dans le secret GitHub DEPLOYER_SA"
  value       = var.enable_github_wif ? google_service_account.deployer[0].email : ""
}

output "loadgen_ssh" {
  value = var.enable_loadgen ? "gcloud compute ssh lab-loadgen --zone ${var.zone} --tunnel-through-iap" : ""
}
