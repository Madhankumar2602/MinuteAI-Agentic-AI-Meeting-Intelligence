// Regenerates src/api/schema.d.ts from the backend's OpenAPI document.
//
// The schema is exported from the FastAPI app object directly, so no server
// needs to be running. Types are then generated with openapi-typescript, run
// through npx (pinned) rather than installed: its peer dependency is
// TypeScript 5, while this project uses TypeScript 6, and the generator only
// runs when the API changes.
//
//   npm run gen:api
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const backend = resolve(here, "..", "..", "backend");
const python = join(backend, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const outDir = resolve(here, "..", "src", "api");
const specPath = join(outDir, "openapi.json");

const spec = execFileSync(python, ["-c", "import json; from app.main import app; print(json.dumps(app.openapi(), indent=1))"], {
  cwd: backend,
  encoding: "utf8",
  env: { ...process.env, LOG_LEVEL: "WARNING" },
  maxBuffer: 16 * 1024 * 1024,
});
mkdirSync(outDir, { recursive: true });
writeFileSync(specPath, spec);

// On Windows npx is a .cmd shim, which Node only runs through a shell - and the
// shell splits unquoted arguments at spaces (this repository lives under a path
// containing one). Quote paths explicitly in that case.
const onWindows = process.platform === "win32";
const q = (arg) => (onWindows ? `"${arg}"` : arg);
execFileSync("npx", ["--yes", "openapi-typescript@7.13.0", q(specPath), "-o", q(join(outDir, "schema.d.ts"))], {
  stdio: "inherit",
  shell: onWindows,
});
console.log("API types regenerated from the backend OpenAPI schema.");
