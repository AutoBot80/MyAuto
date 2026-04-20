/**
 * Remove electron/release so electron-builder can repackage (avoids EBUSY on app.asar).
 * Close Dealer Saathi / any Electron from this app before building.
 */
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const releaseDir = path.join(__dirname, "..", "release");

/** Best-effort: stop processes that usually hold locks under electron/release (Windows). */
function killLockingProcessesWin32() {
  const tryKill = (cmd) => {
    try {
      execSync(cmd, { stdio: "ignore", windowsHide: true });
    } catch {
      /* ignore — process may not exist */
    }
  };
  // Packaged app from this project (productName in electron-builder.yml).
  tryKill('taskkill /IM "Dealer Saathi.exe" /F /T');
  // Sidecar spawned by the app (extraResources/sidecar/job_runner.exe).
  tryKill("taskkill /IM job_runner.exe /F /T");
  // Do not taskkill electron.exe — that can close Cursor/VS Code (they are Electron apps).
}

function sleepSync(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    /* spin */
  }
}

function main() {
  if (!fs.existsSync(releaseDir)) {
    return;
  }

  if (process.platform === "win32") {
    killLockingProcessesWin32();
    sleepSync(400);
    for (let attempt = 1; attempt <= 5; attempt++) {
      try {
        execSync(`cmd /c rmdir /s /q "${releaseDir}"`, {
          stdio: "ignore",
          windowsHide: true,
        });
        if (!fs.existsSync(releaseDir)) {
          return;
        }
      } catch {
        // retry
      }
      sleepSync(800 * attempt);
    }
    try {
      fs.rmSync(releaseDir, { recursive: true, force: true });
    } catch {
      // fall through
    }
    if (fs.existsSync(releaseDir)) {
      // Deleting a busy tree often fails; renaming the folder usually succeeds and unblocks the next build.
      const parent = path.dirname(releaseDir);
      const stamp = new Date().toISOString().replace(/[:.]/g, "-");
      const trashDir = path.join(parent, `release._trash_${stamp}`);
      try {
        fs.renameSync(releaseDir, trashDir);
        console.warn(
          "Could not delete electron\\release (something still has a handle). " +
            `Renamed it to ${path.basename(trashDir)} — you can delete that folder later (after reboot if needed). Build will continue.`
        );
        return;
      } catch {
        // fall through
      }
      console.error(
        "Could not delete or rename electron\\release (file in use).\n" +
          "The build itself does not keep a lock after it exits — Explorer, antivirus, Search, or Cursor often do.\n" +
          "Try: (1) Click away from this folder in File Explorer (or close the window).\n" +
          "     (2) Collapse electron\\release in Cursor’s sidebar.\n" +
          "     (3) taskkill /IM \"Dealer Saathi.exe\" /F /T\n" +
          "     (4) Reboot, then delete electron\\release or electron\\release._trash_* manually."
      );
      process.exit(1);
    }
  } else {
    fs.rmSync(releaseDir, { recursive: true, force: true });
  }
}

main();
