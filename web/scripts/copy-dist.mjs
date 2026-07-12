// Copy the Next.js static export (`out/`) into `hermes_cli/web_dist/` so the
// Python backend (hermes_cli/web_server.py mount_spa) serves it unchanged.
// The prior Vite build wrote directly to web_dist via `outDir`; Next's export
// dir is fixed to `out/`, so we sync it here as the final build step.
import { cp, rm, mkdir, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webDir = resolve(here, "..");
const outDir = resolve(webDir, "out");
const distDir = resolve(webDir, "..", "hermes_cli", "web_dist");

try {
  await access(outDir);
} catch {
  console.error(
    `[copy-dist] Next export not found at ${outDir}. Did \`next build\` run?`,
  );
  process.exit(1);
}

await rm(distDir, { recursive: true, force: true });
await mkdir(distDir, { recursive: true });
await cp(outDir, distDir, { recursive: true });

console.log(`[copy-dist] Synced ${outDir} -> ${distDir}`);
