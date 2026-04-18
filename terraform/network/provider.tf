provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      var.tags,
      {
        Project   = var.project_name
        ManagedBy = "terraform"
      }
    )
  }
}

# CloudFront + WAF for CloudFront must be created in us-east-1 (AWS requirement).
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = merge(
      var.tags,
      {
        Project   = var.project_name
        ManagedBy = "terraform"
      }
    )
  }
}
