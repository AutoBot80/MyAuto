rds_backup_retention_period = 15
rds_deletion_protection     = true

rds_instance_class              = "db.t4g.micro"
rds_max_connections_for_alarms  = 45
alarm_notification_email        = "arya_shashank@hotmail.com"
# sqs_alarm_queue_names         = ["your-queue-name"]

# ASG: time before ELB health failures terminate a new instance (seconds). 1200 = 20 min for slow boots.
asg_health_check_grace_period = 1200

app_dotenv_secret_arn = "arn:aws:secretsmanager:ap-south-1:261399254938:secret:saathi/production/dotenv-yobjLa"
# Legacy SSM fallback (ignored when app_dotenv_secret_arn is set):
# app_dotenv_ssm_param = "/saathi/production/dotenv"

enable_cloudfront_waf = true
cloudfront_api_fqdn   = "api.dealersaathi.co.in"
route53_zone_id       = "Z03940782M9LKBGKLHA0U"