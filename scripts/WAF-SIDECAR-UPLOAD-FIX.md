# Fix Print/Queue RTO upload 403 (WAF only)

CloudFront WAF blocks `/sidecar/upload-artifacts` and `/sidecar/push-sale-bundle` because body-size rules are relaxed only for `/uploads`, `/textract`, and `/subdealer-challan`. Add **`/sidecar`** to the same exclusions.

## Do not run a full `terraform apply` right now

Your plan includes **unrelated drift**:

| Resource | Why it shows up | Needed for uploads? |
|----------|-----------------|---------------------|
| `aws_cloudwatch_metric_alarm.ec2_cpu_*` / `ec2_mem_*` | Old instance id in state vs current EC2 | **No** |
| `aws_launch_template.app` | New AMI id in code vs deployed | **No** |
| `aws_wafv2_web_acl.cloudfront[0]` | Add `/sidecar` (+ Terraform rewrites rule blocks) | **Yes** |

Approving the full plan would repoint alarms and bump the launch template AMI. That is separate operational work.

## Option A — Surgical Terraform (one resource)

From `terraform/network`, using the **same** `-var-file` / workspace you always use:

```powershell
terraform plan -target="aws_wafv2_web_acl.cloudfront[0]"
terraform apply -target="aws_wafv2_web_acl.cloudfront[0]"
```

- Only the Web ACL is updated.
- The plan may still show every WAF `rule` as `-` / `+`; that is normal for `aws_wafv2_web_acl`. The **only intentional change** is two new `search_string = "/sidecar"` entries (rules 10 and 11).

## Option B — No Terraform (recommended if you are worried)

Patch live WAF in **us-east-1** (CloudFront scope):

```powershell
cd "c:\Users\arya_\OneDrive\Desktop\My Auto.AI"
python scripts/patch_waf_sidecar_upload.py --dry-run
python scripts/patch_waf_sidecar_upload.py
```

Requires `boto3` and AWS credentials with `wafv2:GetWebACL` / `wafv2:UpdateWebACL`.

Repo `terraform/network/cloudfront_waf.tf` already includes `/sidecar` so future Terraform runs stay aligned.

## Option C — AWS Console (manual)

1. **WAF & Shield** → **Global (CloudFront)** → Web ACL **`saathi-cf-waf`** → **Edit**.
2. Rule **`CommonRuleSetUploadsExclusions`** (priority 11): scope statement **OR** → **Add condition** → URI path **starts with** `/sidecar` (use lowercase transform if offered).
3. Rule **`AWSManagedRulesCommonRuleSet`** (priority 10): scope-down **NOT (OR …)** → add the same **`/sidecar`** OR branch.
4. **Save**.

## Verify

```powershell
cd "c:\Users\arya_\OneDrive\Desktop\My Auto.AI\Testing Wrappers"
.\Test-PushSaleBundle.ps1 -ApiBase "https://api.dealersaathi.co.in" -Jwt $Jwt -DealerId 100001 -Subfolder "9057397169_210526"
```

Expect `files_written: 5` instead of CloudFront 403.

Also deploy backend when ready so `push-sale-bundle` exists on EC2 (`Update-Prod-App-Backend.ps1`).
