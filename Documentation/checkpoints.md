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

## Rollback / Restore

- Preview checkpoint list: `git log --grep="^Checkpoint:" --pretty=format:"%h %ad %s" --date=iso-strict`
- Restore exact state (detached HEAD): `git switch --detach <commit-or-tag>`
- Restore on current branch (destructive to current uncommitted work): `git reset --hard <commit-or-tag>`
- Safer rollback with branch: `git switch -c restore/<name> <commit-or-tag>`

