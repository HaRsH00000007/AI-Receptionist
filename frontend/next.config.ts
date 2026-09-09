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
};

export default config;
