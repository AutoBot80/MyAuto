# App-tier EC2 instances (for CPU / memory alarms). Refreshes as ASG replaces instances.

data "aws_instances" "app_tier" {
  filter {
    name   = "tag:aws:autoscaling:groupName"
    values = [aws_autoscaling_group.app.name]
  }
}

locals {
  app_instance_ids_sorted = sort(data.aws_instances.app_tier.ids)
  # Must not use length(app_instance_ids_sorted) here: data.aws_instances ids are unknown at plan
  # time until instances exist, which breaks count on aws_cloudwatch_metric_alarm.*.
  ec2_metrics_enabled = var.asg_desired_capacity > 0

  # Metric math: MAX(m0,m1) is parsed as MAX(single tuple) → ValidationException. Use MAX(m0, m1)
  # (spaces). AVG has the same issue; for two instances use (m0+m1)/2. ASG max is 2 in variables.tf.
  _cw_app_n = length(local.app_instance_ids_sorted)
  cw_ec2_max_expr = local._cw_app_n == 0 ? "0" : local._cw_app_n == 1 ? "m0" : "MAX(m0, m1)"
  cw_ec2_avg_expr = local._cw_app_n == 0 ? "0" : local._cw_app_n == 1 ? "m0" : "(m0+m1)/2"
}

# RDS FreeableMemory: fixed byte thresholds (AWS metric is bytes).
locals {
  rds_freeable_memory_warn_bytes = 200 * 1024 * 1024
  rds_freeable_memory_crit_bytes = 128 * 1024 * 1024
  rds_db_connections_warn        = var.rds_max_connections_for_alarms * 0.70
  rds_db_connections_crit        = var.rds_max_connections_for_alarms * 0.85
}
