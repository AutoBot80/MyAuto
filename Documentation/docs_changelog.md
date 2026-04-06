# Documentation staging changelog (`docs_changelog`)

**Purpose:** Short, append-only notes about **what should change** in canonical docs (**BRD**, **HLD**, **LLD**, **`Database DDL.md`**) *before* those files are edited. Agents and humans can read **only this file** (plus touched code) to plan doc updates instead of re-scanning full BRD/HLD/LLD every time—**lower token use** and faster alignment.

**Not a substitute for:** the formal version/changelog tables inside BRD, HLD, LLD, and Database DDL. Those remain the audit trail after work is merged into documentation.

---

## How to use

1. **During implementation:** Append a row or bullet under **Pending** (below): date, area (`BRD` / `HLD` / `LLD` / `DDL` / `API` / `client`), one-line **what** changed, optional pointers (`backend/...`, `§6.1a`, LLD `6.x`).
2. **When updating canonical docs:** Apply edits to BRD/HLD/LLD/`Database DDL.md`, add their normal changelog rows, then **truncate** the **Pending** section (delete completed items) or replace it with a single **Last synced** line (date + optional git short hash).
3. **Optional:** Keep one line in **Last synced** after each truncate so the next session knows the staging log was cleared intentionally.

---

## Pending

_Add entries below. Remove them after the corresponding BRD/HLD/LLD/DDL updates land._

| Date (IST) | Doc / area | Summary |
|------------|------------|---------|
| | | |

---

## Last synced

- **2026-04-05** — File created. Prior doc work (e.g. BR-21 Run Report PDFs, `hero_dms_form22_print`, LLD 6.276) is already in BRD/HLD/LLD/DDL; no backlog copied here.

---

## Token / efficiency note

**Yes:** Reading ~1–2 KB here first is cheaper than pulling large BRD/LLD sections repeatedly. **Caveat:** For unfamiliar or cross-cutting changes, you still need the relevant doc sections or codebase; this file reduces *repeat* context, not the need for accuracy checks.
