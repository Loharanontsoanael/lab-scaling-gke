# VPC dédié (jamais le réseau "default"), sous-réseau avec plages secondaires
# pour les pods et services (cluster VPC-native).
resource "google_compute_network" "vpc" {
  name                    = "lab-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.this]
}

resource "google_compute_subnetwork" "nodes" {
  name                     = "lab-nodes"
  network                  = google_compute_network.vpc.id
  region                   = var.region
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Nœuds privés (sans IP publique) → sortie Internet via Cloud NAT
resource "google_compute_router" "router" {
  name    = "lab-router"
  network = google_compute_network.vpc.id
  region  = var.region
}

resource "google_compute_router_nat" "nat" {
  name                               = "lab-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

# SSH uniquement via Identity-Aware Proxy (pas de port 22 ouvert sur Internet)
resource "google_compute_firewall" "allow_iap_ssh" {
  name          = "lab-allow-iap-ssh"
  network       = google_compute_network.vpc.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["loadgen"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
