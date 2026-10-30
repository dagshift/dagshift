resource "google_composer_environment" "this" {
  name   = "edp"
  region = "us-central1"
  config {
    software_config {
      image_version = "composer-3-airflow-2.10.5-build.15"
      airflow_config_overrides = {
        "webserver-dag_default_view" = "graph"
      }
    }
  }
}

resource "aws_mwaa_environment" "this" {
  name            = "edp"
  airflow_version = "2.10.3"
}
