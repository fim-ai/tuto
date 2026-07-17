"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_CHECK_API || "https://api.tuto.fim.ai";

type Job = {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  stage?: string | null;
  queue_ahead?: number;
  result?: CheckResult;
  error?: string;
};

type CheckResult = {
  arxiv_id: string;
  title: string;
  year: number;
  summary: {
    references_total: number;
    l1: {
      exists: number;
      minor_mismatch: number;
      unparseable: number;
      not_found_raw: number;
      suspicious_leads: number;
      triage?: Record<string, number>;
      llm_judge?: Record<string, number>;
    };
    l2: {
      judged: number;
      claim_cites: number;
      capped: boolean;
      first_pass_flags: number;
      refuted_by_arbiter: number;
      confirmed_leads: number;
    };
  };
  leads: {
    existence: {
      index: number | null;
      raw: string | null;
      rationale: string | null;
      confidence: number | null;
    }[];
    support: {
      citing_sentence: string | null;
      cited_title: string | null;
      claim_span: string | null;
      final_support: string | null;
      error_class: string | null;
      rationale: string | null;
      confidence: number | null;
    }[];
  };
};

const STAGES: { key: string; label: string }[] = [
  { key: "fetching paper", label: "Fetching the paper from arXiv" },
  {
    key: "extracting references",
    label: "Extracting the bibliography and citing sentences",
  },
  {
    key: "checking existence",
    label: "Checking every reference against DBLP and Cito",
  },
  {
    key: "triaging unresolved references",
    label: "Triaging unresolved references",
  },
  {
    key: "judging claim support",
    label: "Reading the cited papers and judging claim support",
  },
  { key: "arbitrating flags", label: "Second-stage review of every flag" },
];

const EXAMPLES: { id: string; label: string }[] = [
  { id: "1706.03762", label: "Attention Is All You Need" },
  { id: "1810.04805", label: "BERT" },
  { id: "1512.03385", label: "ResNet" },
  { id: "2005.14165", label: "GPT-3" },
  { id: "2203.02155", label: "InstructGPT" },
  { id: "2106.09685", label: "LoRA" },
  { id: "2310.06825", label: "Mistral 7B" },
];

export default function CheckClient() {
  const [input, setInput] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const poll = useCallback(async (jobId: string) => {
    try {
      const r = await fetch(`${API_BASE}/check/${jobId}`);
      if (!r.ok) return;
      const j: Job = await r.json();
      setJob(j);
      if (j.status === "done" || j.status === "error") {
        if (timer.current) clearInterval(timer.current);
        timer.current = null;
      }
    } catch {
      /* transient; keep polling */
    }
  }, []);

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  async function doSubmit(value: string) {
    setError(null);
    setJob(null);
    setSubmitting(true);
    try {
      const r = await fetch(`${API_BASE}/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ arxiv_id: value }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => null);
        throw new Error(body?.detail || `request failed (${r.status})`);
      }
      const { job_id } = await r.json();
      await poll(job_id);
      timer.current = setInterval(() => poll(job_id), 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "request failed");
    } finally {
      setSubmitting(false);
    }
  }

  const running = job && (job.status === "queued" || job.status === "running");
  const result = job?.status === "done" ? job.result : undefined;
  const stageIdx = Math.max(
    0,
    STAGES.findIndex((s) => s.key === job?.stage)
  );

  return (
    <div>
      <form
        className="check-form"
        onSubmit={(e) => {
          e.preventDefault();
          doSubmit(input);
        }}
      >
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="arXiv id or URL, e.g. 2405.12345"
          aria-label="arXiv id or URL"
          disabled={!!running || submitting}
        />
        <button
          type="submit"
          className="btn"
          disabled={!input.trim() || !!running || submitting}
        >
          {running ? "Running…" : "Check citations"}
        </button>
      </form>

      <div className="check-examples">
        <span>Try one:</span>
        {EXAMPLES.map((ex) => (
          <button
            key={ex.id}
            type="button"
            className="check-chip"
            disabled={!!running || submitting}
            onClick={() => {
              setInput(ex.id);
              doSubmit(ex.id);
            }}
          >
            {ex.label}
          </button>
        ))}
      </div>

      {error && <p className="check-error">{error}</p>}

      {running && (
        <div className="check-progress">
          {job.status === "queued" ? (
            <p>
              {job.queue_ahead
                ? `Queued, ${job.queue_ahead} ahead of you`
                : "Queued"}
              <span aria-hidden>…</span>
            </p>
          ) : (
            <>
              <p>
                Step {stageIdx + 1} of {STAGES.length}
              </p>
              <ol className="check-steps">
                {STAGES.map((s, i) => (
                  <li
                    key={s.key}
                    className={
                      i < stageIdx
                        ? "done"
                        : i === stageIdx
                          ? "current"
                          : "todo"
                    }
                  >
                    <span className="check-step-mark" aria-hidden>
                      {i < stageIdx ? "✓" : i === stageIdx ? "•" : "·"}
                    </span>
                    {s.label}
                    {i === stageIdx && <span aria-hidden>…</span>}
                  </li>
                ))}
              </ol>
              <div
                className="check-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={STAGES.length}
                aria-valuenow={stageIdx + 1}
              >
                <div
                  className="check-bar-fill"
                  style={{
                    width: `${((stageIdx + 1) / STAGES.length) * 100}%`,
                  }}
                />
              </div>
            </>
          )}
          <p className="check-note">
            A full check reads the cited papers and takes a few minutes. Leave
            this tab open, or come back and resubmit the same id: results are
            kept for a week.
          </p>
        </div>
      )}

      {job?.status === "error" && (
        <p className="check-error">
          This one did not go through: {job.error}. Reference extraction fails
          on some PDFs; that is a limitation, not a finding about the paper.
        </p>
      )}

      {result && <Result r={result} />}
    </div>
  );
}

function Result({ r }: { r: CheckResult }) {
  const { l1, l2 } = r.summary;
  const triage = l1.triage ?? {};
  const judge = l1.llm_judge ?? {};
  const total = r.summary.references_total;
  // An index hit at L1, plus the not_found residue that fuzzy rescue matched
  // to a real paper anyway.
  const matched = l1.exists + l1.minor_mismatch + (triage.exists ?? 0);
  const unmatched = total - matched;
  const nonPaper = (triage.non_paper ?? 0) + (judge.non_paper ?? 0);
  const garbled = (judge.garbled ?? 0) + l1.unparseable;
  const readCites = l2.claim_cites;
  const existenceLeads = r.leads.existence.length;
  const supportLeads = r.leads.support.length;
  const clean = existenceLeads + supportLeads === 0;

  // Health is the share of the checks we could actually run that came back
  // without a lead. Entries we could not parse are excluded from the
  // denominator rather than counted against the paper: they were never checked.
  const checks = total - garbled + readCites;
  const health = checks
    ? Math.round((100 * (checks - existenceLeads - supportLeads)) / checks)
    : null;
  const band =
    health === null
      ? ""
      : health === 100
        ? "no leads in anything we could check"
        : health >= 95
          ? "a few leads worth a look"
          : "several leads worth a look";
  const coverage = total ? Math.round((100 * matched) / total) : 0;

  const residue: string[] = [];
  if (nonPaper)
    residue.push(
      `${nonPaper} ${nonPaper === 1 ? "is not a paper" : "are not papers"} at all (a URL, software, a dataset)`
    );
  if (garbled)
    residue.push(
      `${garbled} ${garbled === 1 ? "was" : "were"} too garbled to parse`
    );
  const rest = unmatched - nonPaper - garbled;
  if (rest > 0) residue.push(`${rest} we simply could not find`);

  const funnel = [
    {
      n: total,
      label: "references in the bibliography",
      sub: "every entry we could extract from the PDF.",
    },
    {
      n: matched,
      label: "matched to a real paper",
      sub: unmatched
        ? `found in DBLP or Cito, or rescued by fuzzy match. Of the ${unmatched} that did not match, ${residue.join(", ")}.`
        : "every entry resolved to a paper that exists.",
    },
    {
      n: readCites,
      label: "claims read against the cited paper",
      sub: `matched references whose citing sentence makes a checkable claim, judged against the cited paper's full text${l2.capped ? ", capped at 60 per paper" : ""}. The other ${matched - readCites} are nominal citations, or we could not retrieve the text to read.`,
    },
  ];

  return (
    <div className="check-result">
      <h2>
        {r.title} <span className="check-year">({r.year})</span>
      </h2>

      {health !== null && (
        <div className="check-health">
          <div className="check-score">
            <span className={clean ? "num" : "num accent"}>{health}</span>
            <span className="denom">/100</span>
          </div>
          <div className="check-score-text">
            <strong>Citation health: {band}</strong>
            <span>
              The share of our {checks} checks that came back clean. It scores
              what we could verify, not the paper: {coverage}% of the
              bibliography matched an index, and only matched references get
              their claims read.
            </span>
          </div>
        </div>
      )}

      <ol className="check-funnel">
        {funnel.map((f) => (
          <li key={f.label}>
            <div className="fn-head">
              <span className="fn-num">{f.n}</span>
              <span className="fn-label">{f.label}</span>
            </div>
            <div className="fn-track" aria-hidden>
              <div
                className="fn-fill"
                style={{ width: `${total ? (100 * f.n) / total : 0}%` }}
              />
            </div>
            <p className="fn-sub">{f.sub}</p>
          </li>
        ))}
      </ol>

      <div className="check-figures">
        <div>
          <span className={existenceLeads ? "num accent" : "num"}>
            {existenceLeads}
          </span>
          <span className="label">
            <strong>references that may not exist</strong>
            <br />
            not found in any index, and triage still finds them suspicious
          </span>
        </div>
        <div>
          <span className={supportLeads ? "num accent" : "num"}>
            {supportLeads}
          </span>
          <span className="label">
            <strong>claims the cited paper does not back</strong>
            <br />
            the reference is real, but it does not say what it is cited for
          </span>
        </div>
      </div>

      <p className="check-disclosure">
        These are <strong>leads for human review, not verdicts</strong>. In our
        ACL 2026 audit the first-pass detector was 13% precise before
        arbitration, and only 2 of 12 human-reviewed existence leads were real.
        Every lead below already survived a second-stage review that tried to
        refute it{l2.refuted_by_arbiter > 0 && (
          <>
            {" "}
            ({l2.refuted_by_arbiter} first-pass{" "}
            {l2.refuted_by_arbiter === 1 ? "flag was" : "flags were"} refuted
            and discarded)
          </>
        )}
        . Read the{" "}
        <a href="/report">methodology</a> before acting on any of them.
      </p>

      {clean && (
        <p className="check-clean">
          No leads. Every checkable reference resolved, and no claim citation
          was confirmed unsupported. This does not certify the paper: nominal
          citations, unverifiable claims, and references outside our indexes
          are out of scope.
        </p>
      )}

      {r.leads.existence.length > 0 && (
        <section>
          <h3>Existence leads</h3>
          <p className="check-note">
            References we could not find in DBLP, Cito, or by fuzzy rescue, and
            which an LLM triage still considers suspicious. Most such leads in
            our audit turned out to be metadata errors, not fabrications.
          </p>
          {r.leads.existence.map((l, i) => (
            <div className="check-lead" key={i}>
              <p className="check-raw">{l.raw}</p>
              <p className="check-why">{l.rationale}</p>
            </div>
          ))}
        </section>
      )}

      {r.leads.support.length > 0 && (
        <section>
          <h3>Support leads</h3>
          <p className="check-note">
            Claim citations where the cited paper, read in full where
            available, did not appear to back the claim, confirmed by a
            second-stage reviewer instructed to refute the flag.
          </p>
          {r.leads.support.map((l, i) => (
            <div className="check-lead" key={i}>
              <p className="check-claim">
                “{l.claim_span || l.citing_sentence}”
              </p>
              <p className="check-cites">
                cites: <em>{l.cited_title}</em>
                {l.final_support && (
                  <span className="check-tag">{l.final_support}</span>
                )}
              </p>
              <p className="check-why">{l.rationale}</p>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
