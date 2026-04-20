/**
 * Run electron-builder on Windows.
 *
 * Key tricks that avoid persistent pain on Windows:
 *
 * 1. Output to a FRESH timestamped folder (e.g. release-20260419-141500)
 *    so we never need to delete the old locked "release" folder.
 *    After a successful build the old folders are cleaned up best-effort.
 *
 * 2. Pre-populate the winCodeSign cache so 7-Zip never needs to extract
 *    macOS .dylib symlinks (fails without Developer Mode / admin).
 *
 * 3. Disable certificate auto-discovery (no code-signing cert on dev machines).
 *
 * When you add a real cert, set CSC_IDENTITY_AUTO_DISCOVERY=true or remove it.
 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const ensureWinCodeSignCache = require("./ensure-wincodesign-cache");

const root = path.join(__dirname, "..");

function stamp() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}

function cleanOldReleases(currentDir) {
  const parent = path.dirname(currentDir);
  const currentName = path.basename(currentDir);
  let entries;
  try {
    entries = fs.readdirSync(parent);
  } catch {
    return;
  }
  for (const name of entries) {
    if (name === currentName) continue;
    if (!name.startsWith("release")) continue;
    const full = path.join(parent, name);
    try {
      fs.rmSync(full, { recursive: true, force: true });
      console.log(`  Cleaned old: ${name}`);
    } catch {
      console.log(`  Could not clean ${name} (locked — will be cleaned next time or after reboot)`);
    }
  }
}

async function main() {
  await ensureWinCodeSignCache();

  process.env.CSC_IDENTITY_AUTO_DISCOVERY = "false";

  // Ensure update-token.json exists so extraResources doesn't fail on local builds.
  const tokenFile = path.join(root, "resources", "update-token.json");
  if (!fs.existsSync(tokenFile)) {
    fs.mkdirSync(path.join(root, "resources"), { recursive: true });
    fs.writeFileSync(tokenFile, JSON.stringify({ token: process.env.GH_TOKEN || "" }));
  }

  const outName = `release-${stamp()}`;
  const outDir = path.join(root, outName);
  console.log(`Output directory: ${outName}`);

  const gh = (process.env.GH_TOKEN || "").trim();
  if (!gh) {
    console.warn(
      "\n[electron-builder] GH_TOKEN is not set — build will succeed but GitHub publish will be skipped or fail.\n" +
        "Set it in the user environment or run the deploy script after injecting the token.\n"
    );
  } else {
    console.log("[electron-builder] GH_TOKEN is set — upload to GitHub will be attempted after the installer is built.\n");
  }

  // Use the programmatic API instead of CLI to avoid shell word-splitting
  // on paths that contain spaces (e.g. "My Auto.AI").
  // electron-builder auto-reads electron-builder.yml from projectDir;
  // we only override directories.output to point at the fresh folder.
  //
  // IMPORTANT: Without `publish: "always"`, local runs leave publish policy undefined, so
  // PublishManager sets isPublish=false and NOTHING is uploaded (unlike CI: `--publish always`).
  const builder = require("electron-builder");
  try {
    await builder.build({
      projectDir: root,
      targets: builder.Platform.WINDOWS.createTarget("nsis", builder.Arch.x64),
      publish: "always",
      config: {
        directories: { output: outName },
      },
    });

    console.log(`\nBuild succeeded! Output: ${outDir}`);
    console.log("Cleaning old release folders...");
    cleanOldReleases(outDir);
  } catch (e) {
    console.error("electron-builder failed:", e.message || e);
    process.exit(1);
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
