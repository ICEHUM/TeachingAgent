import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { build } from "vite";
import reactPlugin from "@vitejs/plugin-react";
import ts from "typescript";

const project = dirname(dirname(fileURLToPath(import.meta.url)));
const cache = join(project, ".cache", "frontend-verification");
await mkdir(cache, { recursive: true });
for (const role of ["student", "teacher"]) {
  const req = createRequire(join(project, "frontend", role, "package.json"));
  console.log(`${role}: React ${req("react/package.json").version}, ReactDOM ${req("react-dom/package.json").version}, TypeScript ${ts.version}`);
  const fixture = await mkdtemp(join(cache, `${role}-`));
  await writeFile(join(fixture, "index.html"), '<!doctype html><html><head><title>Dependency check</title></head><body><div id="root"></div><script type="module" src="/main.tsx"></script></body></html>');
  await writeFile(join(fixture, "main.tsx"), 'import React from "react"; import { createRoot } from "react-dom/client"; const label: string = "Dependency check"; createRoot(document.getElementById("root")!).render(<h1>{label}</h1>);');
  await build({ root: fixture, configFile: false, plugins: [reactPlugin()], logLevel: "warn", build: { outDir: "dist" } });
  console.log(`${role}: Vite production build OK`);
}
