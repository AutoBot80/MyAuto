# Frontend deploy (Vite → S3 → CloudFront)

Static site bucket and CDN invalidation IDs for this project (not secrets).

| Item | Value |
|------|--------|
| S3 bucket | `dealersaathi-prod-1980` |
| CloudFront distribution ID | `E3FYMUCW328MPO` |

From `client/` after `npm run build`:

```powershell
aws s3 sync dist/ s3://dealersaathi-prod-1980/ --delete
aws cloudfront create-invalidation --distribution-id E3FYMUCW328MPO --paths "/*"
```

Then hard-refresh or use a private window so the browser does not use an old cached bundle.

## CORS (required)

The API only allows origins listed in **`CORS_ORIGINS`** on the app server (`/opt/saathi/backend/.env`). If the SPA is opened at the S3 website URL above, that **exact** origin must appear there (including `http://` and **no** trailing slash), e.g.:

`CORS_ORIGINS=http://dealersaathi-prod-1980.s3-website.ap-south-1.amazonaws.com`

After editing `.env`: `sudo systemctl restart saathi-api`.
