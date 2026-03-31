# Checkpoint Registry

Use this file as the canonical index of named checkpoints.

## How Checkpoints Work

- A checkpoint is a normal git commit with title format: `Checkpoint: <name>`.
- Each checkpoint also gets a git tag: `checkpoint/<slug>`.
- The checkpoint commit captures the full repo state at that point, including documentation files.
- TODOs are stored in the commit body and duplicated here for quick lookup.

## Checkpoints

| Name | Tag | Commit | Created (IST) | TODOs |
|---|---|---|---|---|
| Vehicle Flow working, stuck on add payments | `checkpoint/vehicle-flow-working-stuck-on-add-payments` | `6d261b85af14806f22774836060f8755974abc65` | 2026-03-31T16:12:11+05:30 | a) Fix cubic capcity to remove cc; b) Long time spent on contact screen; c) Add payment getting stuck |
| Filling Vehicle and Enquiry flows | `checkpoint/filling-vehicle-and-enquiry-flows` | `55c53fd48ce7d5efb1ad7659bee6aadfec426b43` | 2026-03-31T20:04:58+05:30 | 1) Hard fail implemented before booking as it was creating multiple bookings; 2) Attach vehicle is still doing vehicle steps redundantly; 3) Extra search for Vehicle before contact enquiry; 4) Contact screen pauses for a few seconds |
| Working vehicle and enquiry code | `checkpoint/working-vehicle-and-enquiry-code` | `783f6168f2b1c36b0f93d4d16a47424dca70400c` | 2026-03-31T22:20:00+05:30 | 1) Fixed extra call for vehicle. Speeded up contact page; 2) Hard coded failure remains before bookings; 3) Need to fix add_vehicle code to remove redundant vehicle steps; 4) Need to put condition to stop the code from creating multiple bookings; 5) Then Create Invoice can be enabled |

## Rollback / Restore

- Preview checkpoint list: `git log --grep="^Checkpoint:" --pretty=format:"%h %ad %s" --date=iso-strict`
- Restore exact state (detached HEAD): `git switch --detach <commit-or-tag>`
- Restore on current branch (destructive to current uncommitted work): `git reset --hard <commit-or-tag>`
- Safer rollback with branch: `git switch -c restore/<name> <commit-or-tag>`

