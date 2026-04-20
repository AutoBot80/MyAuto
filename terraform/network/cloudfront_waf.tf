# -----------------------------------------------------------------------------
# Edge: ACM (us-east-1) + WAFv2 (CloudFront scope, us-east-1) + CloudFront → ALB
# Origin uses HTTP to ALB:80; TLS terminates at CloudFront for viewers.
# -----------------------------------------------------------------------------

check "cloudfront_waf_inputs" {
  assert {
    condition = !var.enable_cloudfront_waf || (
      var.cloudfront_api_fqdn != "" && var.route53_zone_id != ""
    )
    error_message = "When enable_cloudfront_waf is true, set cloudfront_api_fqdn and route53_zone_id (hosted zone for DNS validation + alias)."
  }
}

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

resource "aws_acm_certificate" "cloudfront" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  provider = aws.us_east_1

  domain_name       = var.cloudfront_api_fqdn
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "${var.project_name}-cf-viewer-cert"
  }
}

resource "aws_route53_record" "cloudfront_cert_validation" {
  for_each = local.cloudfront_waf_enabled ? {
    for dvo in aws_acm_certificate.cloudfront[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  name            = each.value.name
  records         = [each.value.record]
  ttl             = 60
  type            = each.value.type
  zone_id         = var.route53_zone_id
}

resource "aws_acm_certificate_validation" "cloudfront" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  provider = aws.us_east_1

  certificate_arn = aws_acm_certificate.cloudfront[0].arn
  validation_record_fqdns = [
    for r in aws_route53_record.cloudfront_cert_validation : r.fqdn
  ]
}

resource "aws_wafv2_web_acl" "cloudfront" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  provider = aws.us_east_1

  name  = "${var.project_name}-cf-waf"
  scope = "CLOUDFRONT"

  default_action {
    allow {}
  }

  # Exclude body-inspection rules that false-positive on multipart file uploads
  # (image/PDF binary data triggers SizeRestrictions_BODY, CrossSiteScripting_BODY,
  #  etc.). Scoped to /uploads and /textract paths only so all other routes stay
  #  fully protected.
  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        scope_down_statement {
          not_statement {
            statement {
              or_statement {
                statement {
                  byte_match_statement {
                    search_string         = "/uploads"
                    positional_constraint = "STARTS_WITH"
                    field_to_match {
                      uri_path {}
                    }
                    text_transformation {
                      priority = 0
                      type     = "LOWERCASE"
                    }
                  }
                }
                statement {
                  byte_match_statement {
                    search_string         = "/textract"
                    positional_constraint = "STARTS_WITH"
                    field_to_match {
                      uri_path {}
                    }
                    text_transformation {
                      priority = 0
                      type     = "LOWERCASE"
                    }
                  }
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  # Separate rule: apply CommonRuleSet to /uploads and /textract paths but
  # exclude the body-inspection rules that break multipart binary uploads.
  rule {
    name     = "CommonRuleSetUploadsExclusions"
    priority = 11

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "SizeRestrictions_BODY"
          action_to_use {
            count {}
          }
        }
        rule_action_override {
          name = "CrossSiteScripting_BODY"
          action_to_use {
            count {}
          }
        }
        rule_action_override {
          name = "GenericRFI_BODY"
          action_to_use {
            count {}
          }
        }

        scope_down_statement {
          or_statement {
            statement {
              byte_match_statement {
                search_string         = "/uploads"
                positional_constraint = "STARTS_WITH"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
            statement {
              byte_match_statement {
                search_string         = "/textract"
                positional_constraint = "STARTS_WITH"
                field_to_match {
                  uri_path {}
                }
                text_transformation {
                  priority = 0
                  type     = "LOWERCASE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-CommonRuleSet-Uploads"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 20

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"

        rule_action_override {
          name = "Log4JRCE_BODY"
          action_to_use {
            count {}
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-KnownBadInputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAnonymousIpList"
    priority = 30

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAnonymousIpList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-AnonymousIp"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesAmazonIpReputationList"
    priority = 40

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesAmazonIpReputationList"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-IpReputation"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesLinuxRuleSet"
    priority = 50

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesLinuxRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-LinuxRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesUnixRuleSet"
    priority = 60

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesUnixRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.project_name}-UnixRuleSet"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.project_name}-cf-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "${var.project_name}-cf-waf"
  }
}

resource "aws_cloudfront_distribution" "api" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.project_name} API (origin ALB)"
  price_class         = var.cloudfront_price_class
  wait_for_deployment = true
  web_acl_id          = aws_wafv2_web_acl.cloudfront[0].arn

  aliases = [var.cloudfront_api_fqdn]

  origin {
    domain_name = aws_lb.public.dns_name
    origin_id   = "alb-${var.project_name}"

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    allowed_methods = [
      "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT",
    ]
    cached_methods = ["GET", "HEAD"]

    target_origin_id       = "alb-${var.project_name}"
    compress               = true
    viewer_protocol_policy = "redirect-to-https"

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    acm_certificate_arn            = aws_acm_certificate.cloudfront[0].arn
    ssl_support_method             = "sni-only"
    minimum_protocol_version       = "TLSv1.2_2021"
    cloudfront_default_certificate = false
  }

  depends_on = [aws_acm_certificate_validation.cloudfront]

  tags = {
    Name = "${var.project_name}-cf-api"
  }
}

resource "aws_route53_record" "cloudfront_api_a" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.cloudfront_api_fqdn
  type    = "A"

  alias {
    name                   = aws_cloudfront_distribution.api[0].domain_name
    zone_id                = aws_cloudfront_distribution.api[0].hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "cloudfront_api_aaaa" {
  count = local.cloudfront_waf_enabled ? 1 : 0

  zone_id = var.route53_zone_id
  name    = var.cloudfront_api_fqdn
  type    = "AAAA"

  alias {
    name                   = aws_cloudfront_distribution.api[0].domain_name
    zone_id                = aws_cloudfront_distribution.api[0].hosted_zone_id
    evaluate_target_health = false
  }
}
