# Per-instance CloudWatch series for ASG members. PutMetricAlarm does not support SEARCH in metric-math; use m0/m1 + MAX/AVG.

data "aws_instances" "app_tier" {
  filter {
    name   = "tag:aws:autoscaling:groupName"
    values = [aws_autoscaling_group.app.name]
  }
}

locals {
  cw_app_asg_name = "${var.project_name}-asg-app"

  app_instance_ids_sorted = sort(data.aws_instances.app_tier.ids)
  # Count gate uses desired_capacity, not list length, so plan-time unknown ids do not break alarm count.
  ec2_metrics_enabled = var.asg_desired_capacity > 0

  # Stack allows max 2 instances. MAX(m0, m1) must use a comma+space, not a tuple, or CloudWatch rejects it.
  _ec2_n           = length(local.app_instance_ids_sorted)
  ec2_cpu_max_expr = local._ec2_n == 0 ? "0" : local._ec2_n == 1 ? "m0" : "MAX(m0, m1)"
  ec2_cpu_avg_expr = local._ec2_n == 0 ? "0" : local._ec2_n == 1 ? "m0" : "(m0+m1)/2"
  # Same m0/m1 / MAX() shape as CPU for mem (separate resources with mem metrics on those ids).
  ec2_mem_max_expr = local.ec2_cpu_max_expr
}

# RDS FreeableMemory: fixed byte thresholds (AWS metric is bytes).
locals {
  rds_freeable_memory_warn_bytes = 175 * 1024 * 1024
  rds_freeable_memory_crit_bytes = 128 * 1024 * 1024
  rds_db_connections_warn        = var.rds_max_connections_for_alarms * 0.70
  rds_db_connections_crit        = var.rds_max_connections_for_alarms * 0.85
}
