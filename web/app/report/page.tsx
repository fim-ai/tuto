import type { Metadata } from "next";
import { Article, readReport } from "@/lib/report";

export const metadata: Metadata = {
  title: "Citation Integrity at ACL 2026: A Full-Corpus Audit",
  description:
    "All 209,985 references of ACL 2026 audited: 2 confirmed fabrications (0.001%, stable across re-draws); a support-defect rate that does not reproduce (0.95% audited vs 5.90% pooled); and the false-positive rates of the auditor itself.",
};

export default function ReportPage() {
  const md = readReport("REPORT-acl-2026.en.md");
  return (
    <main className="article-shell">
      <p className="article-meta">Final report · 2026-07-21</p>
      <Article markdown={md} />
    </main>
  );
}
