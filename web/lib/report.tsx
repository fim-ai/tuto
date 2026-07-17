import fs from "node:fs";
import path from "node:path";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function readReport(name: string): string {
  const p = path.join(process.cwd(), "..", "docs", name);
  return fs.readFileSync(p, "utf-8");
}

export function Article({ markdown }: { markdown: string }) {
  return (
    <div className="article">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
