# Checkpoint Registry

Use this file as the canonical index of named checkpoints.

## How Checkpoints Work

- A checkpoint is a normal git commit with title format: `Checkpoint: <name>` (or a **`docs:`** milestone commit that introduces `Documentation/checkpoint-<slug>.md`, when using that style).
- Each checkpoint gets an **annotated git tag** at the snapshot commit. **Preferred:** `checkpoint/<slug>` (e.g. `checkpoint/in-insurance-filled-kyc-page`). **Legacy:** other tag names (e.g. `all-dms-working`) remain valid; they must still appear in the table below.
- The checkpoint commit captures the full repo state at that point, including documentation files.
- TODOs are stored in the tag message and/or commit body and **must be duplicated** in the **Checkpoints** table for quick lookup.

### Mandatory: nothing is a checkpoint until it is in this table

**Do not finish** creating a checkpoint until **all** of the following are done in the **same session** (same PR or contiguous commits):

1. **Register** — Append one row to the **Checkpoints** table (name, tag, full commit hash, created time in IST, TODOs). If the registry file was not part of the snapshot commit, add the row in a follow-up commit immediately after (still before considering the task done).
2. **Tag** — `git tag -a <tag> <commit> -m "..."` pointing at the checkpoint snapshot commit.
3. **Optional narrative** — `Documentation/checkpoint-<slug>.md` may supplement the registry; it does **not** replace the table row.

Agents and humans: if you create only a tag, or only a standalone markdown file, **without** updating this file, the checkpoint is **invisible** to the canonical list—treat that as an incomplete step.

## Checkpoints

| Name | Tag | Commit | Created (IST) | TODOs |
|---|---|---|---|---|
| Vehicle Flow working, stuck on add payments | `checkpoint/vehicle-flow-working-stuck-on-add-payments` | `6d261b85af14806f22774836060f8755974abc65` | 2026-03-31T16:12:11+05:30 | a) Fix cubic capcity to remove cc; b) Long time spent on contact screen; c) Add payment getting stuck |
| Filling Vehicle and Enquiry flows | `checkpoint/filling-vehicle-and-enquiry-flows` | `55c53fd48ce7d5efb1ad7659bee6aadfec426b43` | 2026-03-31T20:04:58+05:30 | 1) Hard fail implemented before booking as it was creating multiple bookings; 2) Attach vehicle is still doing vehicle steps redundantly; 3) Extra search for Vehicle before contact enquiry; 4) Contact screen pauses for a few seconds |
| Working vehicle and enquiry code | `checkpoint/working-vehicle-and-enquiry-code` | `d3e6a17e396e181701453bc3feb3c69205c40298` | 2026-03-31T22:20:00+05:30 | 1) Fixed extra call for vehicle. Speeded up contact page; 2) Hard coded failure remains before bookings; 3) Need to fix add_vehicle code to remove redundant vehicle steps; 4) Need to put condition to stop the code from creating multiple bookings; 5) Then Create Invoice can be enabled |
| All DMS Working | `all-dms-working` | `6079ea47742aa669e33f499893a49aeb8ead9c0b` | 2026-04-01T17:47:52+05:30 | 1) Testing and bug fixes — exercise Create Invoice, master commit, eligibility, Generate Insurance, insurance fill end-to-end; 2) Edge cases — challans and firm; 3) Proceed with insurance fill (`pre_process` / `main_process` after login / 2W) — see **`Documentation/checkpoint-all-dms-working.md`** |
| In Insurance, filled KYC page | `checkpoint/in-insurance-filled-kyc-page` | `8646fd4dffd56668394f89ddfcc3abe66aebffda` | 2026-04-02T15:30:26+05:30 | 1) The structure has created dirty branching and interactions with fill_dms need to clean that up |
| Reached Main Insurance Page | `checkpoint/reached-main-insurance-page` | `eb0617ae0713375e8c332c430b7362a0b2335b95` | 2026-04-02T21:03:12+05:30 | 1) Fill main page elements; 2) Navigation is still slow in a few places — see **`Documentation/checkpoint-reached-main-insurance-page.md`** |

## Rollback / Restore

- Preview checkpoint list: `git log --grep="^Checkpoint:" --pretty=format:"%h %ad %s" --date=iso-strict`
- Restore exact state (detached HEAD): `git switch --detach <commit-or-tag>`
- Restore on current branch (destructive to current uncommitted work): `git reset --hard <commit-or-tag>`
- Safer rollback with branch: `git switch -c restore/<name> <commit-or-tag>`

