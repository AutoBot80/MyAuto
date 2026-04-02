# Checkpoint: Reached Main Insurance Page

**Registry:** This milestone is listed in **`Documentation/checkpoints.md`** (canonical index) under tag **`checkpoint/reached-main-insurance-page`** (commit **`eb0617ae0713375e8c332c430b7362a0b2335b95`**, annotated tag created **2026-04-02T21:03:12+05:30** IST).

Milestone label for the current state of Hero MISP insurance automation work (KYC / post-login flow progress).

## Follow-up todos

1. **Fill main page elements** — Complete automation for fields and actions on the main insurance page after this milestone.
2. **Navigation is still slow in a few places** — Review waits, `networkidle`, click settle, and frame transitions; tighten where safe.

## Git

Restore this snapshot:

```text
git switch --detach checkpoint/reached-main-insurance-page
```

(or `git reset --hard checkpoint/reached-main-insurance-page` — destructive to uncommitted work)
