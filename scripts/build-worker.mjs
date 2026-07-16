// Bundles admin/dashboard.html + landing/index.html into dist/_worker.js for
// Cloudflare Pages deployment, and copies landing/index.html to dist/ for
// any fallback. Handles inlining so the Worker is fully self-contained.
import { readFileSync, writeFileSync, mkdirSync, copyFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const dist = join(root, "dist");
mkdirSync(dist, { recursive: true });

// 1. Read static assets
const adminHtml = readFileSync(join(root, "admin", "dashboard.html"), "utf8");
const landingHtml = readFileSync(join(root, "landing", "index.html"), "utf8");
const workerSrc = readFileSync(join(root, "src", "worker.js"), "utf8");

// 2. Inline both HTML assets into the worker
const inlinedAdmin = ;

const inlinedLanding = ;

// The worker has  and a  placeholder
let bundle = workerSrc.replace(
  /^import\s*\{[^}]*ADMIN_HTML[^}]*\}\s*from\s*['"]\.\/admin-html\.generated\.js['"];?\s*
?/m,
  inlinedAdmin + "
"
);

bundle = bundle.replace(
  /\/\/ const LANDING_HTML placeholder/,
  inlinedLanding.trim()
);

writeFileSync(join(dist, "_worker.js"), bundle);

// 3. Also copy landing page as a static file (for Pages to pick up if Worker
//    doesn't intercept — though _worker.js takes precedence currently)
try {
  copyFileSync(join(root, "landing", "index.html"), join(dist, "index.html"));
  console.log("Copied landing/index.html → dist/index.html");
} catch (e) {
  console.warn("landing/index.html not found, skipping static landing page");
}

console.log("Built dist/_worker.js (" + bundle.length + " bytes) — ready for wrangler pages deploy");
