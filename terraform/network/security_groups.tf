# When CloudFront is enabled, restrict ALB ingress to the CloudFront origin-facing
# managed prefix list so the ALB DNS name is not reachable directly from the internet
# (traffic must flow through CloudFront + WAF). Falls back to alb_ingress_cidr_blocks
# only when CloudFront is disabled.
data "aws_ec2_managed_prefix_list" "cloudfront_origin" {
  count = local.cloudfront_waf_enabled ? 1 : 0
  name  = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name = "${var.project_name}-sg-alb"
  # NOTE: description is immutable; kept as-is so ingress changes update in-place
  # rather than forcing SG replacement. Actual ingress is locked to CloudFront below.
  description = "Public ALB: HTTP/HTTPS from alb_ingress_cidr_blocks"
  vpc_id      = aws_vpc.main.id

  # CloudFront origin connects to the ALB on HTTP:80 (origin_protocol_policy = http-only).
  ingress {
    description     = "HTTP from CloudFront origin (or alb_ingress_cidr_blocks if CF disabled)"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    cidr_blocks     = local.cloudfront_waf_enabled ? [] : var.alb_ingress_cidr_blocks
    prefix_list_ids = local.cloudfront_waf_enabled ? [data.aws_ec2_managed_prefix_list.cloudfront_origin[0].id] : []
  }

  egress {
    description = "Allow all outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-alb"
  }
}

resource "aws_security_group" "app" {
  name        = "${var.project_name}-sg-app"
  description = "App tier (Nginx/Gunicorn): from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTP from ALB"
    from_port       = var.app_target_port
    to_port         = var.app_target_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "Allow all outbound (NAT for updates, APIs)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-app"
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-sg-rds"
  description = "RDS PostgreSQL: from app SG only"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL from app tier"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  egress {
    description = "Allow outbound (e.g. Secrets, patching)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-sg-rds"
  }
}
