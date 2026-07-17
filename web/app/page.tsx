import Link from "next/link";
import {
  IconArrowRight,
  IconDocCheck,
  IconFunnel,
  IconGauge,
  IconPhantomDoc,
  IconSearch,
  IconStack,
  IconUnlink,
} from "@/components/icons";

const FIGURES = [
  {
    icon: IconStack,
    num: "209,985",
    accent: false,
    label: "references audited across 4,459 papers",
  },
  {
    icon: IconPhantomDoc,
    num: "2",
    accent: false,
    label: "confirmed nonexistent references. Fabrication is not the story",
  },
  {
    icon: IconUnlink,
    num: "16%",
    accent: true,
    label: "of papers carry at least one confirmed unsupported citation",
  },
  {
    icon: IconGauge,
    num: "13%",
    accent: false,
    label: "our own first-pass precision, published, not hidden",
  },
];

export default function Home() {
  return (
    <main>
      <div className="shell">
        <section className="hero">
          <div>
            <h1>
              Nobody checks citations. <em>We checked all of them.</em>
            </h1>
            <p className="lede">
              A full-corpus citation integrity audit of ACL 2026: all 209,985
              references checked for existence, and claim citations verified
              against the cited papers themselves.
            </p>
            <div className="hero-actions">
              <Link href="/report" className="btn">
                Read the report
                <IconArrowRight className="btn-icon" />
              </Link>
              <Link href="/check" className="btn btn-quiet">
                <IconSearch className="btn-icon" />
                Check your paper
              </Link>
            </div>
          </div>
          <div className="hero-figures">
            {FIGURES.map(({ icon: Icon, num, accent, label }) => (
              <div className="figure-row" key={num}>
                <Icon className="figure-icon" />
                <span className={accent ? "num accent" : "num"}>{num}</span>
                <span className="label">{label}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="strip">
          <div>
            <IconDocCheck className="strip-icon" />
            <h2>Existence is a solved problem</h2>
            <p>
              Two verification stages plus human review reduced 210k references
              to 12 leads and 2 confirmed fabrications: 0.001%. The scare
              narrative ends there.
            </p>
          </div>
          <div>
            <IconUnlink className="strip-icon" />
            <h2>Support is the real problem</h2>
            <p>
              Real papers, cited for claims they never make. One in six papers
              carries at least one confirmed case, and existence checks can
              never catch them.
            </p>
          </div>
          <div>
            <IconFunnel className="strip-icon" />
            <h2>False positives are the enemy</h2>
            <p>
              Raw detector output would have overstated the problem 8x. Every
              number we publish survived a refutation-stance second stage, and
              our error rates ship with the code.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
