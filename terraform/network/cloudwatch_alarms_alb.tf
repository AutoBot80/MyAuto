# ALB: latency + RPS → scale-out; 5xx → SNS only; healthy hosts → SNS (degraded only if asg_min_size >= 2).

resource "aws_cloudwatch_metric_alarm" "alb_target_response_time_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-target-response-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Average"
  threshold           = 1.2
  alarm_description   = "WARNING: ALB TargetResponseTime > 1.2s (5 min avg) — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-target-response-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "alb_target_response_time_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-target-response-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "TargetResponseTime"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Average"
  threshold           = 2
  alarm_description   = "CRITICAL: ALB TargetResponseTime > 2s (5 min avg) — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-target-response-crit", Severity = "critical" })
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-5xx-warn"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "WARNING: HTTPCode_Target_5XX_Count >= 5 in 5 min; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-5xx-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-5xx-crit"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "HTTPCode_Target_5XX_Count"
  namespace           = "AWS/ApplicationELB"
  period              = 300
  statistic           = "Sum"
  threshold           = 5
  alarm_description   = "CRITICAL: HTTPCode_Target_5XX_Count >= 5 in 5 min; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-5xx-crit", Severity = "critical" })
}

resource "aws_cloudwatch_metric_alarm" "alb_request_rate_warn" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-request-rate-warn"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "RequestCountPerTarget"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 60
  alarm_description   = "WARNING: RequestCountPerTarget > 60 req/min — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-request-rate-warn", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "alb_request_rate_crit" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-request-rate-crit"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "RequestCountPerTarget"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Sum"
  threshold           = 100
  alarm_description   = "CRITICAL: RequestCountPerTarget > 100 req/min — scale-out; 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_scale_out
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-request-rate-crit", Severity = "critical" })
}

# Exactly one healthy target while min ASG size is 2+ (degraded capacity).
resource "aws_cloudwatch_metric_alarm" "alb_healthy_degraded" {
  count = var.enable_cloudwatch_alarms && var.asg_min_size >= 2 ? 1 : 0

  alarm_name          = "${var.project_name}-alb-healthy-degraded"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  alarm_description   = "WARNING: exactly one healthy target (min_capacity >= 2); 2 of 2"
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "degraded"
    expression  = "IF(h1==1, 1, 0)"
    label       = "ExactlyOneHealthy"
    return_data = true
  }

  metric_query {
    id = "h1"
    metric {
      metric_name = "HealthyHostCount"
      namespace   = "AWS/ApplicationELB"
      period      = 60
      stat        = "Average"
      dimensions = {
        LoadBalancer = aws_lb.public.arn_suffix
        TargetGroup  = aws_lb_target_group.app.arn_suffix
      }
    }
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-healthy-degraded", Severity = "warning" })
}

resource "aws_cloudwatch_metric_alarm" "alb_healthy_crit_none" {
  count = var.enable_cloudwatch_alarms ? 1 : 0

  alarm_name          = "${var.project_name}-alb-healthy-crit-down"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  metric_name         = "HealthyHostCount"
  namespace           = "AWS/ApplicationELB"
  period              = 60
  statistic           = "Minimum"
  threshold           = 1
  alarm_description   = "CRITICAL: HealthyHostCount < 1 (no healthy targets); 2 of 2"
  treat_missing_data  = "notBreaching"

  dimensions = {
    LoadBalancer = aws_lb.public.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }

  alarm_actions = local.alarm_actions_sns
  ok_actions    = local.ok_actions_sns
  tags          = merge(var.tags, { Name = "${var.project_name}-alb-healthy-crit-down", Severity = "critical" })
}
