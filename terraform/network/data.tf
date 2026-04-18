data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  # First two AZs in the region (required for ALB across AZs).
  azs = slice(sort(data.aws_availability_zones.available.names), 0, 2)

  # CloudFront + WAF + ACM (viewer cert in us-east-1) + Route 53 validation.
  cloudfront_waf_enabled = var.enable_cloudfront_waf && var.cloudfront_api_fqdn != "" && var.route53_zone_id != ""
}
