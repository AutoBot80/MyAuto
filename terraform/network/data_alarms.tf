# App-tier EC2 instances (for CPU / memory alarms). Refreshes as ASG replaces instances.

data "aws_instances" "app_tier" {
  filter {
    name   = "tag:aws:autoscaling:groupName"
    values = [aws_autoscaling_group.app.name]
  }
}

locals {
  app_instance_ids_sorted = sort(data.aws_instances.app_tier.ids)
  ec2_metrics_enabled     = var.enable_cloudwatch_alarms && length(local.app_instance_ids_sorted) > 0
}

# RDS FreeableMemory: fixed byte thresholds (AWS metric is bytes).
locals {
  rds_freeable_memory_warn_bytes = 200 * 1024 * 1024
  rds_freeable_memory_crit_bytes = 128 * 1024 * 1024
  rds_db_connections_warn        = var.rds_max_connections_for_alarms * 0.70
  rds_db_connections_crit        = var.rds_max_connections_for_alarms * 0.85
}
