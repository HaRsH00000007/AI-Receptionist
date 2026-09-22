import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  // Traces the exact module graph the server needs, so the Docker runtime
  // stage ships neither node_modules nor the build toolchain. Harmless for
  // `next dev`, which ignores it.
  output: "standalone",
  // The API base is read at build time for the browser bundle. It is public by
  // definition — never put a provider key in a NEXT_PUBLIC_ variable.
  env: {
    NEXT_PUBLIC_API_BASE_URL:
      process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  },
  // The dashboard's earlier sections, folded into Agent and Settings. Kept as
  // redirects so bookmarks and links in old emails still land somewhere
  // sensible. Temporary (307), because these are product decisions that may
  // change, and a permanent redirect is cached by browsers indefinitely.
  async redirects() {
    return [
      { source: "/dashboard/receptionist", destination: "/dashboard/agent", permanent: false },
      { source: "/dashboard/configuration", destination: "/dashboard/agent", permanent: false },
      { source: "/dashboard/phone", destination: "/dashboard/settings/phone", permanent: false },
      { source: "/dashboard/usage", destination: "/dashboard/settings/billing", permanent: false },
      { source: "/dashboard/billing", destination: "/dashboard/settings/billing", permanent: false },
    ];
  },
};

export default config;
