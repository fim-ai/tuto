import type { Metadata } from "next";
import CheckClient from "./CheckClient";

export const metadata: Metadata = {
  title: "Check a paper",
  description:
    "Run the Tuto citation audit on a single arXiv paper: existence checks for every reference, claim-support checks against the cited papers, leads for human review.",
  alternates: { canonical: "/check" },
};

export default function CheckPage() {
  return (
    <main>
      <div className="shell check-shell">
        <h1>Check a paper</h1>
        <p className="lede">
          The same pipeline we ran on all 209,985 references of ACL 2026,
          pointed at one paper of your choice. Paste an arXiv id; we extract
          every reference, verify existence, read the cited papers, and hand
          back leads worth a human look.
        </p>
        <CheckClient />
      </div>
    </main>
  );
}
