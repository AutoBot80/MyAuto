# BRD — Admin Saathi

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

Administrative operations: dealer CRUD, login/role assignments, subdealer discounts, staging recovery, usage analytics, server folder browse, diagnostic logs.

---

## 1. Access control

| Mechanism | Detail |
|-----------|--------|
| Admin role | `roles_ref.admin_flag = Y` on user's roles |
| Dealer scope | `admin_dealer_access_ref` — admin sees only assigned dealers |
| JWT | Required on all `/admin/*` routes |

Non-admin users cannot access Admin Saathi tiles or APIs.

---

## 2. Client pages

| Page ID | Tab | Purpose |
|---------|-----|---------|
| `admin-dealers` | Dealers | CRUD dealers, logins, roles, discounts, insurer/CPA prefs |
| `admin-usage` | Usage | Sales/challan matrices, folder browsers, failure/OCR logs |
| `admin-tools` | Admin Tools | Delete All Data (non-prod) + Staging Tools embed |

---

## 3. Dealers management

| Action | API |
|--------|-----|
| List scoped dealers | `GET /admin/dealers` |
| Create dealer | `POST /admin/dealers` |
| View/edit dealer | `GET/PATCH /admin/dealers/{id}` |
| Portal insurers dropdown | `GET /admin/portal-insurers` |
| Logins for dealer | `GET /admin/dealers/{id}/logins` |
| Upsert login assignments | `PUT /admin/dealers/{id}/login-assignments/upsert` |
| Add login+role | `POST /admin/dealers/{id}/login-roles` |
| Subdealer discounts | `GET/POST /admin/dealers/{id}/discounts` |
| Roles catalog | `GET /admin/roles` |
| Login catalog | `GET /admin/login-catalog` |
| Disable login | `PATCH /admin/logins/{login_id}/active-flag` |

**Editable dealer fields (examples):** `prefer_insurer`, `hero_cpi`, `cpi_reqd`, `insurance_pay`, `dms_siebel_portal` (Siebel URL type: **HMCL** / **ASC**), `subdealer_type`, `rto_name`.

---

## 4. Usage & observability

| Sub-tab | API | Content |
|---------|-----|---------|
| Sales matrix | `GET /admin/usage-dealer-matrix` | 7-day IST sales per scoped dealer |
| Sales folders | `GET /admin/folder-contents` | Browse `upload_scans`, `ocr_output` |
| Challans matrix | same matrix `.challans` | Challan counts |
| Challan folders | `kind=challans` | Global challans directory |
| Failure Logs | `GET /admin/failure-logs` | DMS, insurance, print/RTO, challan failures |
| OCR Logs | `GET /admin/ocr-logs` | Add Sales OCR missing-field runs (15 days) |

Folder download: `GET /admin/folder-file`, `GET /admin/folder-zip`.

Data paths: `GET /admin/data-folders` — resolved local or S3 prefixes.

---

## 5. Staging tools

| Action | API | Purpose |
|--------|-----|---------|
| Search by mobile | `GET /admin/staging/search?dealer_id&mobile` | Find staging row |
| Detail view | `GET /admin/staging/{staging_id}` | Full payload + states |
| Cancel Invoice | `POST /admin/staging/{id}/cancel-invoice` | Roll back masters; reset for re-DMS |
| Ins. Manually Filled | `POST /admin/staging/{id}/insurance-manually-filled` | Set `insurance_state=2` for print resume |

Typed confirmation required for destructive actions.

---

## 6. Data reset

| Rule | Detail |
|------|--------|
| ADM-BR-1 | `POST /admin/reset-all-data` truncates non-reference public tables |
| Preserved | `oem_ref`, `dealer_ref`, all `*ref` tables, `oem_service_schedule` |
| Production | Disabled in UI when `environment_is_production` |

---

## 7. Diagnostic tables

| Table | Purpose |
|-------|---------|
| `process_failure_log` | Upsert by (dealer, process_label, dedupe_key) — latest error |
| `ocr_run_log` | Append-only OCR quality log |
| `admin_dealer_access_ref` | Admin login → dealer scope |

Sidecar writes failures: `POST /sidecar/failure-log`.

---

## 8. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | Full admin surface: dealers, usage, staging tools, folders, logs, access ref |
| 1.0 | Jun 2026 | Initial domain split |
