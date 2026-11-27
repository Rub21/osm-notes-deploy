// Copy the built iD editor (npm @openstreetmap/id) to public/id, so Vite serves it in
// dev and puts it in dist/id for the server. The edit mode loads it only when needed.
// Skips source maps, unminified builds and iD's own pages (~15 MB left of ~73 MB).
import { cpSync, existsSync, rmSync } from "node:fs";
import { basename } from "node:path";

const from = "node_modules/@openstreetmap/id/dist";
const to = "public/id";
const skip = (f) =>
  f.endsWith(".map") || f.endsWith(".unminified.js") || f.endsWith(".module.js") ||
  ["iD.js", "index.html", "land.html"].includes(basename(f));

if (!existsSync(from)) throw new Error(`${from} not found: run npm ci`);
rmSync(to, { recursive: true, force: true });
cpSync(from, to, { recursive: true, filter: (src) => !skip(src) });
console.log(`iD copied to ${to}`);
