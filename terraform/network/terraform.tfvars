rds_backup_retention_period = 15
rds_deletion_protection     = true

rds_instance_class              = "db.t4g.micro"
rds_max_connections_for_alarms  = 45
alarm_notification_email        = "arya_shashank@hotmail.com"
# sqs_alarm_queue_names         = ["your-queue-name"]

app_dotenv_ssm_param = "/saathi/production/dotenv"

enable_cloudfront_waf = true
cloudfront_api_fqdn   = "api.dealersaathi.co.in"
route53_zone_id       = "Z03940782M9LKBGKLHA0U"