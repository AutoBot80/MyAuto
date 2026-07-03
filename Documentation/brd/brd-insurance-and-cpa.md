# BRD — Insurance and CPA

**Version:** 2.0  
**Last Updated:** June 2026  
**Parent:** [README.md](README.md)

Hero MISP **Generate Insurance** (Main policy) and optional **CPA Alliance** third-party portal; separate `insurance_master` rows by `insurance_type`.

---

## 1. Policy types

| `insurance_type` | Channel | View for fill |
|------------------|---------|---------------|
| **Main** | Hero MISP GI | `form_insurance_view` (latest Main row) |
| **CPA** | Alliance CPA portal | `form_cpa_insurance_view` (latest CPA row) |

Unique per `(customer_id, vehicle_id, insurance_year, insurance_type)`.

---

## 2. Business rules

| ID | Rule |
|----|------|
| INS-BR-1 | GI after Create Invoice committed masters |
| INS-BR-2 | Inputs: view + `add_sales_staging.payload_json` via `staging_id` |
| INS-BR-3 | `OCR_To_be_Used.json` — insurer fallback only |
| INS-BR-4 | INSERT on success; duplicate triple+type fails |
| INS-BR-5 | Single UPDATE from post–Issue Policy scrape (Main) |
| INS-BR-6 | `dealer_ref.hero_cpi` — MISP CPA add-on row |
| INS-BR-7 | `dealer_ref.prefer_insurer` — fuzzy override ≥20% |
| INS-BR-8 | CPA enabled when `hero_cpi ≠ Y`, CPA URL in `master_ref`, `cpi_reqd=Y` on staging |
| INS-BR-9 | `dealer_ref.insurance_pay` — CC vs APD payment mode on MISP |
| INS-BR-10 | Traces: `Insurance_Form_Values.txt`, `Playwright_insurance.txt` |

---

## 3. Execution paths

| Step | Electron | Cloud |
|------|----------|-------|
| Hero GI | Sidecar `fill_insurance` | `POST /fill-forms/insurance/hero` |
| CPA Alliance | Sidecar `fill_cpa_alliance_insurance` | `POST /fill-forms/insurance/cpa-alliance` |
| Warm | sidecar `warm_insurance` / `warm_cpa` | `/fill-forms/insurance/warm-browser` |

Sidecar: `/sidecar/insurance/resolve`, `/commit`; `/sidecar/cpa/resolve`, `/commit`.

---

## 4. Staging `insurance_state`

| Value | Meaning |
|-------|---------|
| 0 | Not started |
| 2 | Policy preview submitted / manually filled on portal — **Print RTO may proceed** |
| 3 | GI complete — PDF + `insurance_master` INSERT |

Admin: `POST /admin/staging/{id}/insurance-manually-filled` sets state **2** when operator issues policy outside automation.

---

## 5. Hero MISP sequence

1. Partner Sign In → 2W → New Policy
2. KYC (mobile, OVD, documents or verified-AADHAAR branch)
3. VIN + Submit (`pre_process`)
4. I Agree modal → proposal fill (`main_process`)
5. Optional Proposal Review (prod + env flag)
6. Issue Policy + scrape
7. Print Policy PDF → `{mobile}_Insurance_{ddmmyyyy}.pdf` (IST)
8. `insurance_state=3`

---

## 6. CPA Alliance sequence

1. Enabled when eligibility API returns `cpa_alliance_portal_enabled`
2. Fill from `form_cpa_insurance_view` + staging nominee/insurer
3. Portal URL from `master_ref` where `ref_type='CPA'`
4. Commit CPA certificate number → `insurance_master` row with `insurance_type='CPA'`

---

## 7. Field contract (summary)

| Area | Source |
|------|--------|
| KYC / proposal | `form_insurance_view` + staging + `prefer_insurer` |
| Nominee | staging + view |
| Hero CPI checkbox | `dealer_ref.hero_cpi` |
| Payment mode | `dealer_ref.insurance_pay` |
| CPA nominee | `form_cpa_insurance_view` + staging |

---

## 8. APIs

| Method | Path |
|--------|------|
| POST | `/fill-forms/insurance/hero` |
| POST | `/fill-forms/insurance/cpa-alliance` |
| POST | `/fill-forms/insurance/warm-browser` |
| GET | `/add-sales/dealer-cpa-context` |
| GET | `/add-sales/create-invoice-eligibility` (CPA flags) |

---

## 9. Document control

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | Jun 2026 | insurance_type Main/CPA, CPA sidecar, insurance_state, insurance_pay, manual fill admin |
| 1.0 | Jun 2026 | Initial domain split |
