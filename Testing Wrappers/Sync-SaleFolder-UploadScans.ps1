# One-off: push local Uploaded scans sale folder to EC2 (+ S3 via API upload-artifacts).
# Usage:
#   .\Sync-SaleFolder-UploadScans.ps1 -ApiBase "https://YOUR_API" -Jwt "YOUR_BEARER_TOKEN" -DealerId 100001 -Subfolder "8278671032_160526"
# Optional -LocalRoot "D:\Saathi" if SAATHI base differs.

param(
    [Parameter(Mandatory = $true)]
    [string] $ApiBase,
    [Parameter(Mandatory = $true)]
    [string] $Jwt,
    [Parameter(Mandatory = $true)]
    [int] $DealerId,
    [Parameter(Mandatory = $true)]
    [string] $Subfolder,
    [string] $LocalRoot = "D:\Saathi"
)

$ErrorActionPreference = "Stop"
$uploadsLeaf = "Uploaded scans"
$saleDir = Join-Path (Join-Path $LocalRoot $uploadsLeaf) "$DealerId\$Subfolder"
if (-not (Test-Path -LiteralPath $saleDir -PathType Container)) {
    throw "Sale folder not found: $saleDir"
}

$uri = ($ApiBase.TrimEnd("/")) + "/sidecar/upload-artifacts"
$headers = @{ Authorization = "Bearer $($Jwt.Trim())" }
$ok = 0
$fail = 0

Get-ChildItem -LiteralPath $saleDir -Recurse -File | ForEach-Object {
    $rel = ($_.FullName.Substring($saleDir.Length).TrimStart("\", "/") -replace "\\", "/")
    $relPath = "$Subfolder/$rel"
    $boundary = [System.Guid]::NewGuid().ToString()
    $LF = "`r`n"
    $bodyStream = New-Object System.IO.MemoryStream
    $writer = New-Object System.IO.StreamWriter($bodyStream, [System.Text.Encoding]::UTF8)
    $writeField = {
        param($name, $value)
        $writer.Write("--$boundary$LF")
        $writer.Write("Content-Disposition: form-data; name=`"$name`"$LF$LF")
        $writer.Write("$value$LF")
    }
    & $writeField "dealer_id" ([string]$DealerId)
    & $writeField "tree" "uploads"
    & $writeField "rel_path" $relPath
    $writer.Write("--$boundary$LF")
    $writer.Write("Content-Disposition: form-data; name=`"file`"; filename=`"$($_.Name)`"$LF")
    $writer.Write("Content-Type: application/octet-stream$LF$LF")
    $writer.Flush()
    $bytes = [System.IO.File]::ReadAllBytes($_.FullName)
    $bodyStream.Write($bytes, 0, $bytes.Length)
    $writer.Write("$LF--$boundary--$LF")
    $writer.Flush()
    $body = $bodyStream.ToArray()
    $writer.Dispose()
    $bodyStream.Dispose()
    try {
        Invoke-RestMethod -Uri $uri -Method Post -Headers $headers -ContentType "multipart/form-data; boundary=$boundary" -Body $body | Out-Null
        Write-Host "OK  $relPath"
        $ok++
    } catch {
        Write-Warning "FAIL $relPath — $($_.Exception.Message)"
        $fail++
    }
}

Write-Host ""
Write-Host "Done. Uploaded: $ok  Failed: $fail"
Write-Host "EC2 path: /opt/saathi/uploaded-scans/$DealerId/$Subfolder/ (or SAATHI_BASE_DIR from server .env)"
