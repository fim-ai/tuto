import { ImageResponse } from "next/og";

// Apple touch icons must be raster, so this one is generated at build time
// rather than shipped as the SVG the browsers get from icon.svg.
export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#b23a26",
          color: "#fdfcfa",
          fontSize: 116,
          fontWeight: 700,
          letterSpacing: "-0.04em",
        }}
      >
        T.
      </div>
    ),
    size,
  );
}
