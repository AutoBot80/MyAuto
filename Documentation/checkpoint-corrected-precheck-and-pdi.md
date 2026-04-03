# Checkpoint: Corrected Precheck and PDI

**Registry:** This milestone is listed in **`Documentation/checkpoints.md`** (canonical index) under tag **`checkpoint/corrected-precheck-and-pdi`** (commit **`70c9af892e3143c210aea6a4e1776a0441b7628f`**, annotated tag created **2026-04-03T21:40:32+05:30** IST).

Snapshot covers Siebel **Pre-check** / **PDI** handling (shared **Service Request List:New**, scoped row probes, PDI parity) and related **`siebel_dms_playwright`** updates, including video branch **(2)** contact fields (**Home Phone #**, **Email**, **`#s_vctrl_div`** Address before postal) and **`applyOpenEnquiryFilter`** Open-only behaviour for enquiry sweep (**LLD** **6.245**–**6.246**).

## Follow-up todos

1. **Add enquiry for existing contact** — Still needs fixing.

## Git

Restore this snapshot:

```text
git switch --detach checkpoint/corrected-precheck-and-pdi
```

(or `git reset --hard checkpoint/corrected-precheck-and-pdi` — destructive to uncommitted work)
