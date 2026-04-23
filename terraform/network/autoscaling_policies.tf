# Step scaling (no target tracking). Scale out +1 on load; scale in -1 only on sustained low CPU.
# (AWS does not support `cooldown` on StepScaling policies; ASG `default_cooldown` = 300s paces all scaling.)

resource "aws_autoscaling_policy" "scale_out" {
  name                      = "${var.project_name}-scale-out-step"
  autoscaling_group_name    = aws_autoscaling_group.app.name
  adjustment_type           = "ChangeInCapacity"
  policy_type               = "StepScaling"
  metric_aggregation_type   = "Average"
  estimated_instance_warmup = var.asg_scale_out_warmup_seconds

  step_adjustment {
    metric_interval_lower_bound = 0
    scaling_adjustment          = 1
  }
}

resource "aws_autoscaling_policy" "scale_in" {
  name                   = "${var.project_name}-scale-in-simple"
  autoscaling_group_name = aws_autoscaling_group.app.name
  adjustment_type        = "ChangeInCapacity"
  policy_type            = "SimpleScaling"
  scaling_adjustment     = -1
  cooldown               = var.asg_scale_in_cooldown_seconds
}

locals {
  sns_topic_arn_effective = var.alarm_sns_topic_arn != "" ? var.alarm_sns_topic_arn : (
    length(aws_sns_topic.autoscaling_notifications) > 0 ? aws_sns_topic.autoscaling_notifications[0].arn : ""
  )

  alarm_actions_sns = local.sns_topic_arn_effective != "" ? [local.sns_topic_arn_effective] : []
  ok_actions_sns    = local.alarm_actions_sns

  # SNS + same scale-out policy (OR triggers: any matching alarm can fire scale-out).
  alarm_actions_scale_out = concat(local.alarm_actions_sns, [aws_autoscaling_policy.scale_out.arn])
  # SNS + scale-in policy (low CPU sustained).
  alarm_actions_scale_in = concat(local.alarm_actions_sns, [aws_autoscaling_policy.scale_in.arn])
}
