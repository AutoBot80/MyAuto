# count must depend only on variables (plan-time known). Do not use local.sns_topic_arn_effective here — it includes the
# created topic ARN, which is unknown until apply and breaks count.
resource "aws_autoscaling_notification" "app_lifecycle" {
  count = var.alarm_notification_email != "" || var.alarm_sns_topic_arn != "" ? 1 : 0

  group_names = [aws_autoscaling_group.app.name]

  notifications = [
    "autoscaling:EC2_INSTANCE_LAUNCH",
    "autoscaling:EC2_INSTANCE_TERMINATE",
    "autoscaling:EC2_INSTANCE_LAUNCH_ERROR",
    "autoscaling:EC2_INSTANCE_TERMINATE_ERROR",
  ]

  topic_arn = var.alarm_sns_topic_arn != "" ? var.alarm_sns_topic_arn : aws_sns_topic.autoscaling_notifications[0].arn
}
