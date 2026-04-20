variable "aws_region" {
  type        = string
  description = "AWS region for regional resources (ALB, RDS, EC2, this VPC)."
  default     = "ap-south-1"
}

variable "project_name" {
  type        = string
  description = "Prefix for resource Name tags."
  default     = "saathi"
}

variable "vpc_cidr" {
  type        = string
  description = "IPv4 CIDR for the VPC."
  default     = "10.0.0.0/16"
}

variable "tags" {
  type        = map(string)
  description = "Extra tags applied to supported resources."
  default     = {}
}

# --- Security groups / ALB ---

variable "alb_ingress_cidr_blocks" {
  type        = list(string)
  description = "IPv4 CIDRs allowed to reach the ALB on 80/443. Use 0.0.0.0/0 for public beta; tighten when CloudFront or IP allowlists are fixed."
  default     = ["0.0.0.0/0"]
}

# --- RDS PostgreSQL ---

variable "rds_engine_version" {
  type        = string
  description = "Exact PostgreSQL engine version in this region. In ap-south-1 (checked 2026), 16.x included: 16.6, 16.8, 16.9, 16.10–16.13; default is latest stable pin below."
  default     = "16.13"
}

variable "rds_instance_class" {
  type        = string
  description = "RDS instance class (e.g. db.t4g.micro)."
  default     = "db.t4g.micro"
}

variable "rds_db_name" {
  type        = string
  description = "Initial database name."
  default     = "saathi"
}

variable "rds_username" {
  type        = string
  description = "Master username (password is managed in Secrets Manager when manage_master_user_password is true)."
  default     = "saathi_admin"
}

variable "rds_allocated_storage" {
  type        = number
  description = "Allocated storage (GiB), gp3."
  default     = 20
}

variable "rds_max_allocated_storage" {
  type        = number
  description = "Max storage for autoscaling (GiB). Set equal to rds_allocated_storage to disable autoscaling."
  default     = 100
}

variable "rds_skip_final_snapshot" {
  type        = bool
  description = "If true, no final snapshot on destroy (easier teardown; not for prod data you care about)."
  default     = true
}

variable "rds_deletion_protection" {
  type        = bool
  description = "Enable deletion protection on the RDS instance."
  default     = true
}

variable "rds_backup_retention_period" {
  type        = number
  description = "Automated backup retention in days (1-35 for RDS). Point-in-time restore is available within this window."
  default     = 15
}

# --- ALB target group (until HTTPS + ACM are wired) ---

variable "app_health_check_path" {
  type        = string
  description = "Health check path for the ALB target group (FastAPI /health)."
  default     = "/health"
}

variable "app_target_port" {
  type        = number
  description = "Instance port Nginx listens on (HTTP) for the target group."
  default     = 80
}

# --- App tier (EC2 / ASG) ---

variable "ec2_instance_type" {
  type        = string
  description = "Instance type for app tier (x86 Amazon Linux 2023 AMI)."
  default     = "t3.medium"
}

variable "asg_min_size" {
  type        = number
  description = "ASG minimum instances."
  default     = 1
}

variable "asg_desired_capacity" {
  type        = number
  description = "ASG desired instances."
  default     = 1
}

variable "asg_max_size" {
  type        = number
  description = "ASG maximum instances (must be <= 2 for this stack)."
  default     = 2

  validation {
    condition     = var.asg_max_size >= 1 && var.asg_max_size <= 2
    error_message = "asg_max_size must be between 1 and 2."
  }
}

variable "asg_health_check_grace_period" {
  type        = number
  description = "Seconds after instance launch before failed ELB health checks count against ASG replacement (user_data + app warm-up)."
  default     = 300
}

variable "asg_scale_out_warmup_seconds" {
  type        = number
  description = "Step scaling: estimated instance warmup (matches scale-out cooldown intent)."
  default     = 300
}

variable "asg_scale_in_cooldown_seconds" {
  type        = number
  description = "Simple scaling policy cooldown for scale-in (-1 instance)."
  default     = 900
}

# --- App deployment (user_data bootstrap) ---

variable "app_git_repo_url" {
  type        = string
  description = "HTTPS clone URL for the application repo. For private repos, store a GitHub PAT in SSM (see app_github_pat_ssm_param)."
  default     = "https://github.com/AutoBot80/MyAuto"
}

variable "app_github_pat_ssm_param" {
  type        = string
  description = "SSM Parameter Store name holding a GitHub PAT (SecureString) for cloning private repos. Leave empty for public repos."
  default     = ""
}

variable "app_dotenv_ssm_param" {
  type        = string
  description = "DEPRECATED — use app_dotenv_secret_arn instead. SSM Parameter Store name holding the full .env file content (SecureString). Ignored when app_dotenv_secret_arn is set."
  default     = ""
}

variable "app_dotenv_secret_arn" {
  type        = string
  description = "Secrets Manager secret ARN holding the full .env file as a plaintext string. On boot, user_data writes it to /opt/saathi/backend/.env. Takes precedence over app_dotenv_ssm_param."
  default     = ""
}

variable "app_git_branch" {
  type        = string
  description = "Git branch to check out on the app server."
  default     = "main"
}

# --- CloudWatch (RDS + ALB + EC2 + SQS) ---

variable "enable_cloudwatch_alarms" {
  type        = bool
  description = "Create CloudWatch alarms (warning + critical tiers where applicable)."
  default     = true
}

variable "alarm_sns_topic_arn" {
  type        = string
  description = "Optional existing SNS topic ARN. If empty and alarm_notification_email is set, Terraform creates autoscaling-notifications + email subscription."
  default     = ""
}

variable "alarm_notification_email" {
  type        = string
  description = "Email for SNS subscription (CloudWatch + ASG lifecycle). Confirm subscription after apply."
  default     = ""
  sensitive   = true
}

variable "sns_autoscaling_notifications_topic_name" {
  type        = string
  description = "SNS topic name when created by Terraform (must be unique per account/region)."
  default     = "autoscaling-notifications"
}

variable "sqs_alarm_queue_names" {
  type        = list(string)
  description = "SQS queue names for backlog + scale-out triggers (ApproximateNumberOfVisibleMessages)."
  default     = []
}

variable "rds_max_connections_for_alarms" {
  type        = number
  description = "Approximate max_connections for DatabaseConnections %% alarms (db.t3.micro typical ~45)."
  default     = 45
}

variable "rds_alarm_free_storage_min_bytes" {
  type        = number
  description = "Alarm when RDS FreeStorageSpace (free bytes remaining) falls below this value. Default 5 GiB. Independent of volume autoscaling max (e.g. at 100 GiB volume, ~95 GiB used implies ~5 GiB free)."
  default     = 5368709120 # 5 GiB (5 * 1024^3)
}

variable "enable_ec2_memory_alarms" {
  type        = bool
  description = "EC2 memory alarms require CloudWatch Agent mem_used_percent in CWAgent."
  default     = true
}

# --- CloudFront (edge) + WAF + ACM (viewer cert must be in us-east-1) ---

variable "enable_cloudfront_waf" {
  type        = bool
  description = "When true, create ACM (us-east-1), WAF Web ACL (us-east-1, CloudFront scope), and CloudFront distribution in front of the ALB. Requires cloudfront_api_fqdn and route53_zone_id for DNS-validated ACM."
  default     = false
}

variable "cloudfront_api_fqdn" {
  type        = string
  description = "API hostname served at CloudFront, e.g. api.dealersaathi.co.in (must match ACM / Route 53)."
  default     = ""
}

variable "route53_zone_id" {
  type        = string
  description = "Route 53 hosted zone ID for dealersaathi.co.in (or parent zone). Required when enable_cloudfront_waf is true so ACM DNS validation and alias records can be managed."
  default     = ""
}

variable "cloudfront_price_class" {
  type        = string
  description = "CloudFront price class. PriceClass_200 includes India edge locations."
  default     = "PriceClass_200"
}
