# EC2: AWS/EC2 CPU (per InstanceId) + CWAgent mem_used_percent (per InstanceId). No SEARCH (unsupported on metric alarms).
# Rerun `terraform apply` when ASG membership changes so the alarm’s m0/m1 list matches in-service instances.

resource "aws_cloudwatch_metric_alarm" "ec2_cpu_warn" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 65
  alarm_description   = "WARNING: max EC2 CPUUtilization across ASG > 65% (5 min) — 2 of 2; scale-out"
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = [for i, iid in local.app_instance_ids_sorted : { idx = i, id = iid }]
    content {
      id = "m${metric_query.value.idx}"
      metric {
        metric_name = "CPUUtilization"
        namespace   = "AWS/EC2"
        period      = 300
        stat        = "Average"
        dimensions = {
          InstanceId = metric_query.value.id
        }
      }
    }
  }

  metric_query {
    id          = "qout"
    expression  = local.ec2_cpu_max_expr
    label       = "MaxCPU"
    return_data = true
    period      = 300
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_cpu_crit" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 80
  alarm_description   = "CRITICAL: max EC2 CPUUtilization > 80% (5 min) — 2 of 2; scale-out"
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = [for i, iid in local.app_instance_ids_sorted : { idx = i, id = iid }]
    content {
      id = "m${metric_query.value.idx}"
      metric {
        metric_name = "CPUUtilization"
        namespace   = "AWS/EC2"
        period      = 300
        stat        = "Average"
        dimensions = {
          InstanceId = metric_query.value.id
        }
      }
    }
  }

  metric_query {
    id          = "qout"
    expression  = local.ec2_cpu_max_expr
    label       = "MaxCPU"
    return_data = true
    period      = 300
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-crit", Severity = "critical" })
}

# Scale-in: average CPU over 15 min; same instance list as m0, m1.
resource "aws_cloudwatch_metric_alarm" "ec2_cpu_scale_in" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-scale-in"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 35
  alarm_description   = "Scale-in: avg EC2 CPUUtilization < 35% (15 min) — 2 of 2; asg=${local.cw_app_asg_name}"
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = [for i, iid in local.app_instance_ids_sorted : { idx = i, id = iid }]
    content {
      id = "m${metric_query.value.idx}"
      metric {
        metric_name = "CPUUtilization"
        namespace   = "AWS/EC2"
        period      = 900
        stat        = "Average"
        dimensions = {
          InstanceId = metric_query.value.id
        }
      }
    }
  }

  metric_query {
    id          = "qavg"
    expression  = local.ec2_cpu_avg_expr
    label       = "AvgCPU"
    return_data = true
    period      = 900
  }

  alarm_actions = local.alarm_actions_scale_in
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-scale-in", Severity = "scaling" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_mem_warn" {
  count = var.enable_cloudwatch_alarms && var.enable_ec2_memory_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-mem-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 75
  alarm_description   = "WARNING: max mem_used_percent (CWAgent) > 75% (5 min) — 2 of 2; per-InstanceId"
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = [for i, iid in local.app_instance_ids_sorted : { idx = i, id = iid }]
    content {
      id = "m${metric_query.value.idx}"
      metric {
        metric_name = "mem_used_percent"
        namespace   = "CWAgent"
        period      = 300
        stat        = "Average"
        dimensions = {
          InstanceId = metric_query.value.id
        }
      }
    }
  }

  metric_query {
    id          = "qmax"
    expression  = local.ec2_mem_max_expr
    label       = "MaxMemPct"
    return_data = true
    period      = 300
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-mem-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_mem_crit" {
  count = var.enable_cloudwatch_alarms && var.enable_ec2_memory_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-mem-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 85
  alarm_description   = "CRITICAL: max mem_used_percent (CWAgent) > 85% (5 min) — 2 of 2; per-InstanceId"
  treat_missing_data  = "notBreaching"

  dynamic "metric_query" {
    for_each = [for i, iid in local.app_instance_ids_sorted : { idx = i, id = iid }]
    content {
      id = "m${metric_query.value.idx}"
      metric {
        metric_name = "mem_used_percent"
        namespace   = "CWAgent"
        period      = 300
        stat        = "Average"
        dimensions = {
          InstanceId = metric_query.value.id
        }
      }
    }
  }

  metric_query {
    id          = "qmax"
    expression  = local.ec2_mem_max_expr
    label       = "MaxMemPct"
    return_data = true
    period      = 300
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-mem-crit", Severity = "critical" })
}
