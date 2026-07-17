import { ImageResponse } from "next/og";

// The report is meant to be shared; without this the link previews as a blank card.
export const alt = "Tuto: we checked all 209,985 references in ACL 2026";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const FIGURES = [
  ["209,985", "references audited"],
  ["2", "confirmed nonexistent"],
  ["16%", "papers with an unsupported citation"],
];

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#fafaf9",
          color: "#1c1c1a",
          padding: "72px 76px",
        }}
      >
        <div style={{ display: "flex", fontSize: 30, fontWeight: 700 }}>
          Tuto<span style={{ color: "#b23a26" }}>.</span>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "column",
            fontSize: 68,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: "-0.02em",
          }}
        >
          <div style={{ display: "flex" }}>Nobody checks citations.</div>
          <div style={{ display: "flex", color: "#b23a26" }}>We checked all of them.</div>
        </div>

        <div style={{ display: "flex", gap: 56, borderTop: "1px solid #e3e3df", paddingTop: 28 }}>
          {FIGURES.map(([num, label]) => (
            <div key={num} style={{ display: "flex", flexDirection: "column" }}>
              <div style={{ fontSize: 40, fontWeight: 700 }}>{num}</div>
              <div style={{ fontSize: 20, color: "#55554f", marginTop: 4 }}>{label}</div>
            </div>
          ))}
        </div>
      </div>
    ),
    size,
  );
}
