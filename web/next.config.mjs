import path from "node:path";
import { fileURLToPath } from "node:url";

// The site reads ../docs/*.md, so the project root must be the repo root, not web/.
// Turbopack otherwise infers web/ (where the lockfile sits) and rejects the glob.
const repoRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** @type {import('next').NextConfig} */
const nextConfig = {
  turbopack: { root: repoRoot },
  outputFileTracingIncludes: {
    '/report': ['../docs/*.md'],
    '/report/zh': ['../docs/*.md'],
  },
};
export default nextConfig;
