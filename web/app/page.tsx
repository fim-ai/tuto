import Link from "next/link";

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
              </Link>
              <Link href="/check" className="btn btn-quiet">
                Check your paper
              </Link>
            </div>
          </div>
          <div className="hero-figures">
            <div className="figure-row">
              <span className="num">209,985</span>
              <span className="label">references audited across 4,459 papers</span>
            </div>
            <div className="figure-row">
              <span className="num">2</span>
              <span className="label">
                confirmed nonexistent references. Fabrication is not the story
              </span>
            </div>
            <div className="figure-row">
              <span className="num accent">16%</span>
              <span className="label">
                of papers carry at least one confirmed unsupported citation
              </span>
            </div>
            <div className="figure-row">
              <span className="num">13%</span>
              <span className="label">
                our own first-pass precision, published, not hidden
              </span>
            </div>
          </div>
        </section>

        <section className="strip">
          <div>
            <h2>Existence is a solved problem</h2>
            <p>
              Two verification stages plus human review reduced 210k references
              to 12 leads and 2 confirmed fabrications: 0.001%. The scare
              narrative ends there.
            </p>
          </div>
          <div>
            <h2>Support is the real problem</h2>
            <p>
              Real papers, cited for claims they never make. One in six papers
              carries at least one confirmed case, and existence checks can
              never catch them.
            </p>
          </div>
          <div>
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
