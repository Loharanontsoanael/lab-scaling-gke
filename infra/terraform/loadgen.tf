# VM génératrice de charge, HORS du cluster testé mais dans la même région :
# la charge ne vole pas de CPU aux pods mesurés, et la latence réseau reste faible
# (plus fiable qu'un test depuis une connexion domestique).
resource "google_service_account" "loadgen" {
  count        = var.enable_loadgen ? 1 : 0
  account_id   = "lab-loadgen"
  display_name = "Générateur de charge"
}

resource "google_compute_instance" "loadgen" {
  count        = var.enable_loadgen ? 1 : 0
  name         = "lab-loadgen"
  machine_type = "e2-standard-4"
  zone         = var.zone
  tags         = ["loadgen"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.nodes.id
    # Pas d'IP publique : SSH via IAP, sortie via Cloud NAT
  }

  service_account {
    email  = google_service_account.loadgen[0].email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot = true
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e
    apt-get update -qq
    apt-get install -y -qq python3-venv rsync
    python3 -m venv /opt/locust
    /opt/locust/bin/pip install -q locust==2.32.4
    ln -sf /opt/locust/bin/locust /usr/local/bin/locust
  EOT

  depends_on = [google_compute_router_nat.nat]
}
