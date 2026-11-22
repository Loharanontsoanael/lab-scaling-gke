# Compte de service dédié aux nœuds (moindre privilège, pas le compte Compute par défaut)
resource "google_service_account" "nodes" {
  account_id   = "lab-gke-nodes"
  display_name = "Nœuds GKE du lab"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  name     = var.cluster_name
  location = var.zone

  # On gère le pool de nœuds séparément (bonne pratique : modifiable sans recréer le cluster)
  remove_default_node_pool = true
  initial_node_count       = 1

  network         = google_compute_network.vpc.id
  subnetwork      = google_compute_subnetwork.nodes.id
  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # API Kubernetes joignable depuis ton PC
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  vertical_pod_autoscaling {
    enabled = true
  }

  # Dataplane V2 (eBPF/Cilium) : applique les NetworkPolicy
  datapath_provider = "ADVANCED_DATAPATH"

  addons_config {
    http_load_balancing {
      disabled = false
    }
    horizontal_pod_autoscaling {
      disabled = false
    }
  }

  deletion_protection = false # cluster de lab : on le détruit après chaque session

  depends_on = [google_project_service.this]
}

resource "google_container_node_pool" "main" {
  name       = "main"
  cluster    = google_container_cluster.this.id
  location   = var.zone
  node_count = var.node_count

  # Cluster Autoscaler : ajoute/retire des VMs selon les pods en attente
  autoscaling {
    min_node_count = var.min_nodes
    max_node_count = var.max_nodes
  }

  management {
    auto_repair  = true # recrée un nœud en panne (démo HA)
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.machine_type
    disk_size_gb    = 30
    disk_type       = "pd-balanced"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = { pool = "main" }
  }

  lifecycle {
    # Le Cluster Autoscaler modifie node_count : Terraform ne doit pas le remettre à 3
    ignore_changes = [node_count]
  }
}
