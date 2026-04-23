# SQS backlog: SNS + scale-out on warn/crit thresholds.

resource "aws_cloudwatch_metric_alarm" "sqs_depth_warn" {
  for_each = var.enable_cloudwatch_alarms && length(var.sqs_alarm_queue_names) > 0 ? toset(var.sqs_alarm_queue_names) : toset([])

  alarm_name          = "${var.project_name}-sqs-depth-warn-${replace(replace(each.value, ".fifo", ""), "/", "-")}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "ApproximateNumberOfVisibleMessages"
  namespace           = "AWS/SQS"
  period              = 600
  statistic           = "Average"
  threshold           = 500
  alarm_description   = "WARNING: SQS ${each.value} backlog — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-sqs-depth-warn", Queue = each.value, Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "sqs_depth_crit" {
  for_each = var.enable_cloudwatch_alarms && length(var.sqs_alarm_queue_names) > 0 ? toset(var.sqs_alarm_queue_names) : toset([])

  alarm_name          = "${var.project_name}-sqs-depth-crit-${replace(replace(each.value, ".fifo", ""), "/", "-")}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "ApproximateNumberOfVisibleMessages"
  namespace           = "AWS/SQS"
  period              = 600
  statistic           = "Average"
  threshold           = 2000
  alarm_description   = "CRITICAL: SQS ${each.value} backlog — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-sqs-depth-crit", Queue = each.value, Severity = "critical" })
}
