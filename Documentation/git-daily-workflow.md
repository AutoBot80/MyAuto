# Daily Git Workflow

Keep the repository updated on a daily basis by committing and pushing your work.

---

## Option 1: Manual commands

Run these in PowerShell from the project root:

```powershell
cd "C:\Users\arya_\OneDrive\Desktop\My Auto.AI"

git add .
git status
git commit -m "Daily update: $(Get-Date -Format 'yyyy-MM-dd')"
git push origin main
```

- **`git status`** — Review what will be committed before you run `git commit`.
- If there are no changes, `git commit` will say "nothing to commit"; that's fine.

---

## Option 2: Run the daily update script

From the project root:

```powershell
.\scripts\daily-git-update.ps1
```

The script will add all changes, commit with today's date, and push to `origin main`. It will skip commit if there are no changes.

---

## What gets committed

- All modified and new files under `client/`, `backend/`, `Documentation/`, `workers/` (and the root files that aren’t ignored).
- **Not** committed: `venv/`, `node_modules/`, `.env`, `backend/.env`, `Scanner/` (see root `.gitignore`).

---

## If you haven’t pushed in a while

Ensure your branch is up to date with the remote before pushing:

```powershell
git pull origin main --rebase
git push origin main
```

---

## Option 3: Daily startup (Git + uvicorn + npm run dev)

Run **daily_startup.bat** from the project root (double-click or from a terminal). It will:

1. Run a daily Git update (add, commit with today’s date, push).
2. Start the backend (uvicorn) in a new Command Prompt window.
3. Start the client (`npm run dev`) in another new Command Prompt window.

To create **daily_startup.exe** from the batch file:

1. Use a Bat-to-EXE converter, e.g.:
   - **Bat to Exe Converter** (https://www.f2ko.de/en/b2e.php), or
   - **Quick Batch File Compiler**, or any similar tool.
2. Open **daily_startup.bat** in the converter.
3. Set output to **daily_startup.exe** and place the exe in the project root.
4. When running the exe, start it from the project root (or set “Start in” to the project root in the converter so the script finds `venv`, `backend`, `client`).

---

## Document control

| Version | Date       | Changes                |
|---------|------------|------------------------|
| 0.1     | March 2025 | Initial daily workflow |
