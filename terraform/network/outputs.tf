output "account_id" {
  description = "AWS account ID (sanity check)."
  value       = data.aws_caller_identity.current.account_id
}

output "vpc_id" {
  description = "VPC ID for ALB, RDS subnet groups, security groups."
  value       = aws_vpc.main.id
}

output "vpc_cidr" {
  value = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Use for internet-facing ALB (two AZs)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Use for EC2 app tier and RDS private subnets."
  value       = aws_subnet.private[*].id
}

output "nat_gateway_id" {
  description = "Single NAT in AZ 0 (cost-optimized beta pattern)."
  value       = aws_nat_gateway.main.id
}

output "availability_zones" {
  value = local.azs
}

# --- Security groups (for EC2 / ASG launch template) ---

output "security_group_alb_id" {
  description = "Attach to internet-facing ALB (already set on aws_lb.public)."
  value       = aws_security_group.alb.id
}

output "security_group_app_id" {
  description = "Attach to EC2 instances running Nginx/Gunicorn."
  value       = aws_security_group.app.id
}

output "security_group_rds_id" {
  description = "RDS uses this; reference only."
  value       = aws_security_group.rds.id
}

# --- ALB ---

output "alb_arn" {
  value = aws_lb.public.arn
}

output "alb_dns_name" {
  description = "Use as CloudFront origin (or CNAME target) before custom domain."
  value       = aws_lb.public.dns_name
}

output "alb_zone_id" {
  description = "Route 53 alias target hosted zone ID for the ALB."
  value       = aws_lb.public.zone_id
}

output "alb_target_group_arn" {
  description = "ASG registers instances here automatically."
  value       = aws_lb_target_group.app.arn
}

# --- App tier (ASG) ---

output "asg_name" {
  value = aws_autoscaling_group.app.name
}

output "launch_template_id" {
  value = aws_launch_template.app.id
}

output "ec2_app_instance_profile_name" {
  description = "IAM instance profile attached to app instances."
  value       = aws_iam_instance_profile.ec2_app.name
}

# --- RDS ---

output "rds_endpoint" {
  description = "PostgreSQL host:port for DATABASE_URL (private)."
  value       = aws_db_instance.main.endpoint
}

output "rds_port" {
  value = aws_db_instance.main.port
}

output "rds_identifier" {
  value = aws_db_instance.main.identifier
}

output "rds_engine_version" {
  description = "Engine version on the RDS instance (matches var.rds_engine_version unless AWS normalizes minor)."
  value       = aws_db_instance.main.engine_version
}

output "rds_master_user_secret_arn" {
  description = "Secrets Manager ARN for the master password (retrieve to build DATABASE_URL or rotate)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
  sensitive   = true
}

# --- CloudFront + WAF (optional; enable_cloudfront_waf + FQDN + Route 53 zone) ---

output "cloudfront_distribution_id" {
  description = "CloudFront distribution ID (null if edge stack not created)."
  value       = length(aws_cloudfront_distribution.api) > 0 ? aws_cloudfront_distribution.api[0].id : null
}

output "cloudfront_domain_name" {
  description = "CloudFront domain name (*.cloudfront.net) for the API distribution."
  value       = length(aws_cloudfront_distribution.api) > 0 ? aws_cloudfront_distribution.api[0].domain_name : null
}

output "cloudfront_api_url" {
  description = "HTTPS URL clients should use when the edge stack is enabled (custom FQDN)."
  value       = local.cloudfront_waf_enabled ? "https://${var.cloudfront_api_fqdn}" : null
}

output "waf_cloudfront_web_acl_arn" {
  description = "WAFv2 Web ACL ARN attached to CloudFront (us-east-1)."
  value       = length(aws_wafv2_web_acl.cloudfront) > 0 ? aws_wafv2_web_acl.cloudfront[0].arn : null
}

output "acm_cloudfront_certificate_arn" {
  description = "ACM certificate ARN in us-east-1 used by CloudFront (viewer TLS)."
  value       = length(aws_acm_certificate.cloudfront) > 0 ? aws_acm_certificate.cloudfront[0].arn : null
}

output "cloudfront_origin_note" {
  description = "Operational note for tightening the ALB security group after CloudFront is live."
  value       = "Consider restricting ALB:80 ingress to the CloudFront origin-facing managed prefix list (com.amazonaws.global.cloudfront.origin-facing) so the load balancer is not reachable directly from the internet."
}
