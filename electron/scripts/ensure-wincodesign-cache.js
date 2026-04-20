/**
 * Pre-populate the electron-builder winCodeSign cache so the build never
 * needs to extract the .7z archive (which fails on Windows without
 * Developer Mode / admin due to macOS .dylib symlinks in the archive).
 *
 * Downloads the GitHub *source* zip (regular zip, no symlinks) and copies
 * the winCodeSign subfolder into the cache.  Once the cache directory
 * exists, electron-builder skips the download entirely.
 *
 * See: https://github.com/electron-userland/electron-builder/issues/8149
 */
const fs = require("fs");
const path = require("path");
const https = require("https");
const { execSync } = require("child_process");
const os = require("os");

const CACHE_DIR = path.join(
  process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"),
  "electron-builder",
  "Cache",
  "winCodeSign",
  "winCodeSign-2.6.0"
);

const SOURCE_ZIP_URL =
  "https://github.com/electron-userland/electron-builder-binaries/archive/refs/tags/winCodeSign-2.6.0.zip";

function download(url, dest) {
  return new Promise((resolve, reject) => {
    const follow = (u) => {
      https.get(u, { headers: { "User-Agent": "electron-builder-cache" } }, (res) => {
        if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          follow(res.headers.location);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode} for ${u}`));
          return;
        }
        const ws = fs.createWriteStream(dest);
        res.pipe(ws);
        ws.on("finish", () => ws.close(resolve));
        ws.on("error", reject);
      }).on("error", reject);
    };
    follow(url);
  });
}

async function main() {
  if (process.platform !== "win32") {
    return;
  }

  const marker = path.join(CACHE_DIR, "windows-10");
  if (fs.existsSync(marker)) {
    console.log("winCodeSign cache already populated:", CACHE_DIR);
    return;
  }

  console.log("Populating winCodeSign cache (avoids symlink extraction error)...");

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "wincodesign-"));
  const zipPath = path.join(tmpDir, "winCodeSign-2.6.0.zip");

  try {
    console.log("  Downloading source zip...");
    await download(SOURCE_ZIP_URL, zipPath);

    console.log("  Extracting...");
    const extractDir = path.join(tmpDir, "extracted");
    fs.mkdirSync(extractDir, { recursive: true });
    execSync(
      `powershell -NoProfile -Command "Expand-Archive -LiteralPath '${zipPath}' -DestinationPath '${extractDir}' -Force"`,
      { stdio: "pipe", windowsHide: true }
    );

    const innerDir = path.join(
      extractDir,
      "electron-builder-binaries-winCodeSign-2.6.0",
      "winCodeSign"
    );
    if (!fs.existsSync(innerDir)) {
      throw new Error(`Expected folder not found: ${innerDir}`);
    }

    fs.mkdirSync(CACHE_DIR, { recursive: true });
    execSync(`xcopy /E /I /Y /Q "${innerDir}" "${CACHE_DIR}"`, {
      stdio: "pipe",
      windowsHide: true,
    });

    console.log("  winCodeSign cache ready:", CACHE_DIR);
  } finally {
    try {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    } catch {
      /* best effort cleanup */
    }
  }
}

module.exports = main;

if (require.main === module) {
  main().catch((e) => {
    console.error("ensure-wincodesign-cache failed:", e);
    process.exit(1);
  });
}
