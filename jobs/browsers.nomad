job "autowebcompat-browsers" {
  datacenters = ["dc1"]
  type = "service"
  priority = 55

  group "chrome" {
    count = 3
    constraint {
      attribute = "${meta.crawler}"
      value = "true"
    }
    task "chrome" {
      driver = "docker"
      config {
        image = "selenium/node-chrome:3.141.59-neon"
        port_map {
          node = 5555
        }
        volumes = [
          "/dev/shm:/dev/shm",
        ]
      }
      env {
        REMOTE_HOST = "http://${NOMAD_ADDR_node}"
      }
      template {
        data = <<EOF
          {{- range service "selenium-hub" }}
            HUB_HOST = {{ .Address | toJSON}}
            HUB_PORT = {{ .Port | toJSON}}
          {{- end }}
          NODE_MAX_SESSION = 1
          NODE_MAX_INSTANCES = 1
        EOF
        destination = "local/generated.env"
        env = true
      }
      resources {
        memory = 700
        cpu = 1000
        network {
          mbits = 50
          port "node" {}
        }
      }
    }
  }

  group "firefox" {
    count = 3
    constraint {
      attribute = "${meta.crawler}"
      value = "true"
    }
    task "firefox" {
      driver = "docker"
      config {
        image = "selenium/node-firefox:3.141.59-neon"
        port_map {
          node = 5555
        }
        volumes = [
          "/dev/shm:/dev/shm",
        ]
      }
      env {
        REMOTE_HOST = "http://${NOMAD_ADDR_node}"
      }
      template {
        data = <<EOF
          {{- range service "selenium-hub" }}
            HUB_HOST = {{ .Address | toJSON}}
            HUB_PORT = {{ .Port | toJSON}}
          {{- end }}
          NODE_MAX_SESSION = 1
          NODE_MAX_INSTANCES = 1
        EOF
        destination = "local/generated.env"
        env = true
      }
      resources {
        memory = 900
        cpu = 1000
        network {
          mbits = 50
          port "node" {}
        }
      }
    }
  }
}