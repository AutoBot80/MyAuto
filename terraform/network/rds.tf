resource "aws_db_subnet_group" "main" {
  name        = "${var.project_name}-db-subnet"
  description = "Private subnets for RDS"
  subnet_ids  = aws_subnet.private[*].id

  tags = {
    Name = "${var.project_name}-db-subnet"
  }
}

resource "aws_db_instance" "main" {
  identifier = "${var.project_name}-postgres"

  engine         = "postgres"
  engine_version = var.rds_engine_version
  instance_class = var.rds_instance_class

  allocated_storage     = var.rds_allocated_storage
  max_allocated_storage = var.rds_max_allocated_storage > var.rds_allocated_storage ? var.rds_max_allocated_storage : null
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = var.rds_db_name
  username = var.rds_username

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible = false
  multi_az            = false

  backup_retention_period = 7
  copy_tags_to_snapshot   = true

  skip_final_snapshot          = var.rds_skip_final_snapshot
  deletion_protection          = var.rds_deletion_protection
  performance_insights_enabled = false
  monitoring_interval          = 0

  tags = {
    Name = "${var.project_name}-postgres"
  }
}
