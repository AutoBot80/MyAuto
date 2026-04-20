# EC2: CPU warn/crit → scale-out; CPU avg low 15 min → scale-in; memory → SNS only.

resource "aws_cloudwatch_metric_alarm" "ec2_cpu_warn" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 65
  alarm_description   = "WARNING: max EC2 CPU > 65% (5 min) — scale-out"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "cpu_max"
    expression  = length(local.app_instance_ids_sorted) == 1 ? "m0" : "MAX(${join(",", [for i in range(length(local.app_instance_ids_sorted)) : "m${i}"])})"
    label       = "MaxCPU"
    return_data = true
  }

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

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_cpu_crit" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 80
  alarm_description   = "CRITICAL: max EC2 CPU > 80% (5 min) — scale-out"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "cpu_max"
    expression  = length(local.app_instance_ids_sorted) == 1 ? "m0" : "MAX(${join(",", [for i in range(length(local.app_instance_ids_sorted)) : "m${i}"])})"
    label       = "MaxCPU"
    return_data = true
  }

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

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-crit", Severity = "critical" })
}

# Scale-in: average CPU across app instances < 35% for one 15-minute period.
resource "aws_cloudwatch_metric_alarm" "ec2_cpu_scale_in" {
  count = var.enable_cloudwatch_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-cpu-scale-in"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = 35
  alarm_description   = "Scale-in: avg EC2 CPU < 35% over 15 min"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "cpu_avg"
    expression  = length(local.app_instance_ids_sorted) == 1 ? "m0" : "AVG(${join(",", [for i in range(length(local.app_instance_ids_sorted)) : "m${i}"])})"
    label       = "AvgCPU"
    return_data = true
  }

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

  alarm_actions = local.alarm_actions_scale_in
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-cpu-scale-in", Severity = "scaling" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_mem_warn" {
  count = var.enable_cloudwatch_alarms && var.enable_ec2_memory_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-mem-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 75
  alarm_description   = "WARNING: max mem_used_percent (CWAgent) > 75%"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "mem_max"
    expression  = length(local.app_instance_ids_sorted) == 1 ? "m0" : "MAX(${join(",", [for i in range(length(local.app_instance_ids_sorted)) : "m${i}"])})"
    label       = "MaxMemPct"
    return_data = true
  }

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

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-mem-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "ec2_mem_crit" {
  count = var.enable_cloudwatch_alarms && var.enable_ec2_memory_alarms && local.ec2_metrics_enabled ? 1 : 0

  alarm_name          = "${var.project_name}-ec2-mem-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 85
  alarm_description   = "CRITICAL: max mem_used_percent (CWAgent) > 85%"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "mem_max"
    expression  = length(local.app_instance_ids_sorted) == 1 ? "m0" : "MAX(${join(",", [for i in range(length(local.app_instance_ids_sorted)) : "m${i}"])})"
    label       = "MaxMemPct"
    return_data = true
  }

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

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-ec2-mem-crit", Severity = "critical" })
}
