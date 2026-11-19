resource "aws_ecs_task_definition" "web" {
  container_definitions = jsonencode([{
    name = "web"
    environment = [{ name = "ORDERS_URL", value = "http://orders.internal:8080" }]
  }])
}
resource "aws_lambda_function" "worker" {
  environment {
    variables = {
      STOCK_HOST = "stock"
      IGNORE_URL = "https://${aws_lb.api.dns_name}"
      USER_NAME = "orders"
    }
  }
}
resource "google_cloud_run_service" "web" {
  template {
    spec {
      containers {
        env {
          name = "CART_ENDPOINT"
          value = "http://cart:8080"
        }
      }
    }
  }
}
resource "kubernetes_service" "orders" {
  metadata {
    name = "orders-api"
  }
  spec {
    selector = { app = "orders" }
  }
}
