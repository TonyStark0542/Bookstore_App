# ==============================================================================
# Terraform Configuration: Monolithic Self-Contained Jenkins Controller Setup
# ==============================================================================

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = "your-gcp-project-id" # 💥 Replace with your actual GCP Project ID
  region  = "us-central1"
  zone    = "us-central1-a"       #
}

# 1. Firewall Rule: Open ports for public browser access
resource "google_compute_firewall" "allow_ci_cd_traffic" {
  name    = "allow-jenkins-and-bookstore"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8080", "5000"] # 8080 (Jenkins UI/Frontend Proxy), 5000 (Frontend UI)
  }

  source_ranges = ["0.0.0.0/0"] 
  target_tags   = ["jenkins-master-node"]
}

# 2. Compute Engine VM Instance
resource "google_compute_instance" "jenkins_controller" {
  name         = "jenkins-ci-cd-controller"
  machine_type = "e2-medium" # Essential tier to run Jenkins and Docker build cycles together
  zone         = "us-central1-a"

  tags = ["jenkins-master-node"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = 35 # Expanded to hold Jenkins configs, Docker caches, and DB updates
    }
  }

  network_interface {
    network = "default"
    access_config {
      // Provision an external public IP address
    }
  }

  # 🔥 INLINED METADATA STARTUP SCRIPT
  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e
    echo "🔄 Initializing complete system-wide provisioning engine..."

    # --------------------------------------------------------------------------
    # STEP 1: Dependencies & Native Repositories Update
    # --------------------------------------------------------------------------
    apt-get update -y
    apt install -y fontconfig openjdk-21-jre

    # --------------------------------------------------------------------------
    # STEP 2: Official Jenkins Installation
    # --------------------------------------------------------------------------
    echo "📥 Registering Jenkins keyrings and package lines..."
    # Download the official trusted repository armor keys
    wget -O /etc/apt/keyrings/jenkins-keyring.asc https://pkg.jenkins.io/debian-stable/jenkins.io-2026.key
    echo "deb [signed-by=/etc/apt/keyrings/jenkins-keyring.asc]" https://pkg.jenkins.io/debian-stable binary/ | sudo tee /etc/apt/sources.list.d/jenkins.list > /dev/null

    # Update maps and install the Jenkins service package
    apt-get update -y
    apt-get install -y jenkins

    # Start and anchor the automation service manager
    systemctl start jenkins
    systemctl enable jenkins

    # --------------------------------------------------------------------------
    # STEP 3: Docker Engine & System Integration
    # --------------------------------------------------------------------------
    echo "🐳 Provisioning native Docker virtualization layer..."
    apt-get install -y docker.io
    systemctl start docker
    systemctl enable docker

    # --------------------------------------------------------------------------
    # STEP 4: Standalone Docker Compose v2 Integration
    # --------------------------------------------------------------------------
    echo "🐙 Dropping Docker Compose v2 binaries globally..."
    mkdir -p /usr/lib/docker/cli-plugins
    curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/lib/docker/cli-plugins/docker-compose
    chmod +x /usr/lib/docker/cli-plugins/docker-compose

    # Create global link patterns so the system path map binds cleanly
    ln -sf /usr/lib/docker/cli-plugins/docker-compose /usr/bin/docker-compose

    # --------------------------------------------------------------------------
    # STEP 5: Permission Boundary Remediation
    # --------------------------------------------------------------------------
    echo "🔐 Linking user security permission boundaries..."
    # Force add the jenkins system account to the docker group
    groupadd -f docker
    usermod -aG docker jenkins

    # Hard reset the automated workspace systems to capture new permission frames
    systemctl restart jenkins
    systemctl restart docker

    echo "🎉 SYSTEM PROVISIONING SUCCESSFUL. RUNWAY CLEAR."
  EOT
}

# 3. Outputs: Displays the live browser connection target endpoints
output "jenkins_url" {
  value       = "http://${google_compute_instance.jenkins_controller.network_interface[0].access_config[0].nat_ip}:8080"
  description = "The direct web address to connect to your new Jenkins Web UI console panel"
}