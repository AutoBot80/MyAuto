# RDS: free disk, CPU, FreeableMemory, DatabaseConnections — SNS only (no ASG scaling). RAM thresholds use rds_instance_class.

resource "aws_cloudwatch_metric_alarm" "rds_free_storage_low" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-free-storage-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = var.rds_alarm_free_storage_min_bytes
  alarm_description   = "RDS free disk space low: FreeStorageSpace < ${var.rds_alarm_free_storage_min_bytes} bytes (warn when fewer than this many bytes free; not total volume size)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-free-storage-low", Severity = "critical" })
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-cpu-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 65
  alarm_description   = "WARNING: RDS CPUUtilization > 65% (5 min avg)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-cpu-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-cpu-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  alarm_description   = "CRITICAL: RDS CPUUtilization > 80% (5 min avg)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-cpu-crit", Severity = "critical" })
}

resource "aws_cloudwatch_metric_alarm" "rds_freeable_memory_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-freeable-memory-warn"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = local.rds_freeable_memory_warn_bytes
  alarm_description   = "WARNING: RDS FreeableMemory < 200 MB (5 min avg)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-freeable-memory-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "rds_freeable_memory_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-freeable-memory-crit"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 3
  datapoints_to_alarm = 2
  metric_name         = "FreeableMemory"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = local.rds_freeable_memory_crit_bytes
  alarm_description   = "CRITICAL: RDS FreeableMemory < 128 MB (5 min avg; 2 of 3 periods)"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = []
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-freeable-memory-crit", Severity = "critical" })
}

resource "aws_cloudwatch_metric_alarm" "rds_db_connections_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-connections-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = local.rds_db_connections_warn
  alarm_description   = "WARNING: DatabaseConnections > 70% of max (${var.rds_max_connections_for_alarms})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-connections-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "rds_db_connections_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-rds-connections-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "DatabaseConnections"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = local.rds_db_connections_crit
  alarm_description   = "CRITICAL: DatabaseConnections > 85% of max (${var.rds_max_connections_for_alarms})"
  treat_missing_data  = "notBreaching"

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-rds-connections-crit", Severity = "critical" })
}
