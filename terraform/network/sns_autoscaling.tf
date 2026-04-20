# SNS topic: email + CloudWatch alarms + Auto Scaling lifecycle notifications.
# Topic name defaults to "autoscaling-notifications". Confirm the email subscription after apply.

locals {
  create_autoscaling_sns_topic = var.alarm_notification_email != "" && var.alarm_sns_topic_arn == ""
}

resource "aws_sns_topic" "autoscaling_notifications" {
  count = local.create_autoscaling_sns_topic ? 1 : 0
  name  = var.sns_autoscaling_notifications_topic_name

  tags = merge(var.tags, { Name = var.sns_autoscaling_notifications_topic_name })
}

data "aws_iam_policy_document" "sns_autoscaling_publish" {
  count = local.create_autoscaling_sns_topic ? 1 : 0

  statement {
    sid    = "AllowCloudWatchAlarms"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.autoscaling_notifications[0].arn]
  }

  statement {
    sid    = "AllowAutoScalingNotifications"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["autoscaling.amazonaws.com"]
    }
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.autoscaling_notifications[0].arn]
  }
}

resource "aws_sns_topic_policy" "autoscaling_notifications" {
  count  = local.create_autoscaling_sns_topic ? 1 : 0
  arn    = aws_sns_topic.autoscaling_notifications[0].arn
  policy = data.aws_iam_policy_document.sns_autoscaling_publish[0].json
}

resource "aws_sns_topic_subscription" "autoscaling_notifications_email" {
  count     = local.create_autoscaling_sns_topic ? 1 : 0
  topic_arn = aws_sns_topic.autoscaling_notifications[0].arn
  protocol  = "email"
  endpoint  = var.alarm_notification_email
}
