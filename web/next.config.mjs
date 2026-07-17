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
  },
  // The zh report was published at /report/zh before English became canonical.
  // Keep the URL alive for anyone holding the old link.
  async redirects() {
    return [{ source: '/report/zh', destination: '/report', permanent: true }];
  },
};
export default nextConfig;
