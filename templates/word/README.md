# Word Templates for Print Forms

Place your Form 20 and Gate Pass Word templates here. They are used when generating PDFs via the Print Forms button.

**Required files:**
- `FORM 20 Template.docx` — Form 20 (front, back, page 3)
- `Gate Pass Template.docx` — Gate Pass for customer delivery

**Placeholders:**
- Form 20: `{{field_0_city}}`, `{{field_1_name}}`, `{{field_2_care_of}}`, `{{field_3_address}}`, `{{field_10_dealer_name}}`, `{{field_14_body_type}}`, `{{field_16_oem_name}}`, `{{field_17_year_of_mfg}}`, `{{field_20_cubic_capacity}}`, `{{field_21_model}}`, `{{field_22_chassis_no}}`, etc.
- Gate Pass: `{{field_0_today_date}}`, `{{field_1_oem_name}}`, `{{field_2_customer_name}}`, `{{field_3_aadhar_id}}`, `{{field_4_model}}`, `{{field_5_color}}`, `{{field_6_key_num}}`, `{{field_7_chassis_num}}`

Override paths via env: `FORM20_TEMPLATE_DOCX`, `GATE_PASS_TEMPLATE_DOCX`.
