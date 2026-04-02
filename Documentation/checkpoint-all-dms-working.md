# Checkpoint: All DMS Working

**Recorded:** 2026-04-01  
**Label:** All DMS Working  

**Registry:** This milestone is listed in **`Documentation/checkpoints.md`** (canonical index) under tag **`all-dms-working`**.

Use this file as the project milestone marker: Siebel / Fill DMS flows, My Orders branching, Playwright log naming, master commit logging, eligibility + Generate Insurance wiring, and MISP login / 2W hardening are in a working state as of the git tag **`all-dms-working`**.

## Follow-up todos

1. **Testing and bug fixes to continue** — exercise Create Invoice, master commit, eligibility, Generate Insurance, and insurance fill end-to-end; fix regressions.
2. **Add edge cases — challans and firm** — extend automation and validation for challan flows and firm-specific edge cases (product detail TBD).
3. **Proceed with insurance fill** — continue Hero MISP **`fill_hero_insurance_service`** / **`pre_process`** / **`main_process`** after login and 2W entry.

## Git

Annotated tag: **`all-dms-working`** points at the commit that contains this file (run **`git show all-dms-working`**). Tag message lists the same todos.

Uncommitted work in your tree (e.g. DMS/insurance code edits) is **not** part of that commit; add and commit separately, then move the tag if you want the label on a fuller snapshot.

If you have additional uncommitted changes you want included in the same snapshot, commit them and move or recreate the tag:

```text
git tag -d all-dms-working
git tag -a all-dms-working -m "All DMS Working — see Documentation/checkpoint-all-dms-working.md"
```
