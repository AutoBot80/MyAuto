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
  description = "RDS instance class (e.g. db.t4g.micro for small beta)."
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
  default     = false
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
  description = "ASG maximum instances."
  default     = 2
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
