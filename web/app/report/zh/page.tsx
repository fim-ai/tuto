import type { Metadata } from "next";
import Link from "next/link";
import { Article, readReport } from "@/lib/report";

export const metadata: Metadata = {
  title: "ACL 2026 引文诚信审计报告",
  description:
    "全量 209,985 条参考文献审计：坐实编造引用 2 条（0.001%），16% 的论文至少含一条经确认的问题引用，以及审计工具自身的误报率。",
};

export default function ReportZhPage() {
  const md = readReport("REPORT-acl-2026-draft.md");
  return (
    <main className="article-shell" lang="zh-CN">
      <p className="article-meta">
        草稿 v0.6 · 2026-07-17 · <Link href="/report">English</Link>
      </p>
      <Article markdown={md} />
    </main>
  );
}
