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
  { id: "2005.14165", label: "GPT-3" },
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
      timer.current = setInterval(() => poll(job_id), 5000);
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
                Step {stageIdx + 1} of {STAGES.length} ·{" "}
                {STAGES[stageIdx]?.label || "Working"}
                <span aria-hidden>…</span>
              </p>
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
  const clean =
    r.leads.existence.length === 0 && r.leads.support.length === 0;
  return (
    <div className="check-result">
      <h2>
        {r.title} <span className="check-year">({r.year})</span>
      </h2>

      <div className="check-figures">
        <div>
          <span className="num">{r.summary.references_total}</span>
          <span className="label">references extracted</span>
        </div>
        <div>
          <span className="num">{l1.exists + l1.minor_mismatch}</span>
          <span className="label">resolved as existing</span>
        </div>
        <div>
          <span className="num">{l2.claim_cites}</span>
          <span className="label">
            claim citations read against the cited paper
            {l2.capped ? " (capped at 60)" : ""}
          </span>
        </div>
        <div>
          <span className={r.leads.support.length ? "num accent" : "num"}>
            {r.leads.existence.length + r.leads.support.length}
          </span>
          <span className="label">leads for your review</span>
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
