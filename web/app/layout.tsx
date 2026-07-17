import type { Metadata } from "next";
import Link from "next/link";
import { IconExternal, IconGitHub, IconSearch } from "@/components/icons";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://tuto.fim.ai"),
  title: {
    default: "Tuto · Citation integrity, verified",
    template: "%s · Tuto",
  },
  description:
    "Full-corpus citation integrity audits for NLP venues. Two-stage verification, published false-positive rates, open pipeline.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <div className="shell">
            <Link href="/" className="wordmark">
              Tuto<span>.</span>
            </Link>
            <nav className="site-nav">
              <Link href="/check">
                <IconSearch className="nav-icon" />
                Check
              </Link>
              <Link href="/report">Report</Link>
              <a
                href="https://cito.fim.ai"
                target="_blank"
                rel="noopener noreferrer"
              >
                Cito
                <IconExternal className="nav-icon nav-icon-trail" />
              </a>
              <a
                href="https://github.com/fim-ai/tuto"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Tuto on GitHub"
                className="nav-github"
              >
                <IconGitHub className="nav-icon" />
              </a>
            </nav>
          </div>
        </header>
        {children}
        <footer className="site-footer">
          <div className="shell">
            <div>
              Tuto · citation auditing by{" "}
              <a href="https://fim.ai" target="_blank" rel="noopener noreferrer">
                fim.ai
              </a>
            </div>
            <div className="footer-links">
              <a
                href="https://github.com/fim-ai/tuto"
                target="_blank"
                rel="noopener noreferrer"
              >
                <IconGitHub className="nav-icon" />
                Source and dataset
              </a>
              <span>Pipeline Apache-2.0 · Dataset CC BY 4.0</span>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
