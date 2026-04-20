data "aws_ami" "al2023_x86" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  # Full self-deploy: system deps → clone repo → venv → pip → Nginx → Gunicorn systemd service.
  # CloudWatch Agent publishes mem_used_percent + disk_used_percent to namespace CWAgent (see cloudwatch_alarms_ec2.tf).
  app_user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    exec > /var/log/user-data.log 2>&1

    # All `aws` CLI calls (including deploy/ec2/load-dotenv.sh) must use this region —
    # otherwise Secrets Manager / SSM default to the wrong region and bootstrap fails.
    export AWS_DEFAULT_REGION="${var.aws_region}"
    export AWS_REGION="${var.aws_region}"

    # ── 1. System packages ───────────────────────────────────────────────
    dnf install -y nginx amazon-cloudwatch-agent git \
      python3.11 python3.11-devel python3.11-pip \
      gcc pkg-config cairo-devel nano htop postgresql15

    # ── 2. CloudWatch Agent ──────────────────────────────────────────────
    mkdir -p /opt/aws/amazon-cloudwatch-agent/etc
    python3 <<'PY'
    import json; cfg={"agent":{"metrics_collection_interval":60,"run_as_user":"root"},"metrics":{"namespace":"CWAgent","append_dimensions":{"InstanceId":"$${aws:InstanceId}"},"metrics_collected":{"mem":{"measurement":["mem_used_percent"]},"disk":{"measurement":["disk_used_percent"],"resources":["*"]}}}}; open("/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json","w").write(json.dumps(cfg,indent=2))
    PY
    /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
      -a fetch-config -m ec2 -s \
      -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
    systemctl enable amazon-cloudwatch-agent
    systemctl restart amazon-cloudwatch-agent

    # ── 3. Nginx — stub health check (replaced after app clone) ──────────
    cat > /etc/nginx/conf.d/saathi-health.conf <<'NGINX'
    server {
      listen 80 default_server;
      server_name _;
      location ${var.app_health_check_path} {
        access_log off;
        default_type text/plain;
        return 200 'ok';
      }
      location / {
        default_type text/plain;
        return 503 'bootstrap: deploying app...';
      }
    }
    NGINX
    rm -f /etc/nginx/conf.d/default.conf
    systemctl enable nginx
    systemctl restart nginx

    # ── 4. Clone application repo ────────────────────────────────────────
    REPO_URL="${var.app_git_repo_url}"
    %{if var.app_github_pat_ssm_param != ""}
    GH_PAT=$(aws ssm get-parameter --name "${var.app_github_pat_ssm_param}" \
      --with-decryption --query "Parameter.Value" --output text --region ${var.aws_region})
    REPO_URL=$(echo "$REPO_URL" | sed "s|https://|https://$${GH_PAT}@|")
    %{endif}
    git clone --branch "${var.app_git_branch}" --single-branch "$REPO_URL" /opt/saathi

    # ── 5. Python venv + dependencies ────────────────────────────────────
    python3.11 -m venv /opt/saathi/backend/venv
    ln -sfn /opt/saathi/backend/venv /opt/saathi/venv
    /opt/saathi/backend/venv/bin/pip install --upgrade pip
    /opt/saathi/backend/venv/bin/pip install -r /opt/saathi/backend/requirements.txt
    # Deployments over SSH use ec2-user; pip must not require sudo (avoid root-owned site-packages).
    chown -R ec2-user:ec2-user /opt/saathi/backend/venv

    # ── 6. .env from Secrets Manager (preferred) or SSM (legacy) ──────────
    # Inline (do not rely on deploy/ec2/load-dotenv.sh in git — remote branch may omit it).
    %{if var.app_dotenv_secret_arn != ""}
    DOTENV_TMP=$(mktemp)
    trap 'rm -f "$DOTENV_TMP"' EXIT
    if ! aws --region ${var.aws_region} secretsmanager get-secret-value \
      --secret-id "${var.app_dotenv_secret_arn}" \
      --query SecretString \
      --output text > "$DOTENV_TMP"; then
      echo "aws secretsmanager get-secret-value failed (IAM, ARN, region)." >&2
      exit 1
    fi
    if [[ ! -s "$DOTENV_TMP" ]]; then
      echo "SecretString was empty — refusing to write .env" >&2
      exit 1
    fi
    mv -f "$DOTENV_TMP" /opt/saathi/backend/.env
    trap - EXIT
    chmod 600 /opt/saathi/backend/.env
    echo "Wrote .env from Secrets Manager ($(wc -l < /opt/saathi/backend/.env) lines)"
    %{else}
    %{if var.app_dotenv_ssm_param != ""}
    aws ssm get-parameter --name "${var.app_dotenv_ssm_param}" \
      --with-decryption --query "Parameter.Value" --output text \
      --region ${var.aws_region} > /opt/saathi/backend/.env
    chmod 600 /opt/saathi/backend/.env
    %{endif}
    %{endif}

    # ── 7. Nginx — real proxy config (replaces stub) ─────────────────────
    rm -f /etc/nginx/conf.d/saathi-health.conf
    cp /opt/saathi/deploy/ec2/nginx-saathi.conf /etc/nginx/conf.d/saathi.conf
    rm -f /etc/nginx/conf.d/default.conf
    nginx -t && systemctl reload nginx

    # ── 8. Systemd service ───────────────────────────────────────────────
    chmod +x /opt/saathi/deploy/ec2/run-gunicorn.sh
    cp /opt/saathi/deploy/ec2/saathi-api.service /etc/systemd/system/saathi-api.service
    systemctl daemon-reload
    systemctl enable saathi-api
    systemctl start saathi-api

    # ── 9. Readiness check (retries for ALB health check grace period) ───
    for i in $(seq 1 12); do
      if curl -sf http://127.0.0.1:8000/health; then
        echo "App healthy after attempt $i"
        break
      fi
      sleep 5
    done
  EOT
}

resource "aws_launch_template" "app" {
  name_prefix   = "${var.project_name}-app-"
  image_id      = data.aws_ami.al2023_x86.id
  instance_type = var.ec2_instance_type

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2_app.name
  }

  vpc_security_group_ids = [aws_security_group.app.id]

  user_data = base64encode(local.app_user_data)

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.project_name}-app"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${var.project_name}-app"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "app" {
  name                      = "${var.project_name}-asg-app"
  vpc_zone_identifier       = aws_subnet.private[*].id
  health_check_type         = "ELB"
  health_check_grace_period = var.asg_health_check_grace_period
  default_cooldown          = 300
  protect_from_scale_in     = false
  min_size                  = var.asg_min_size
  max_size                  = var.asg_max_size
  desired_capacity          = var.asg_desired_capacity

  target_group_arns = [aws_lb_target_group.app.arn]

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "${var.project_name}-asg-app"
    propagate_at_launch = false
  }

  dynamic "tag" {
    for_each = var.tags
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

}
