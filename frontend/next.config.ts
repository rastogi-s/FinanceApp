import type { NextConfig } from "next";

// A static export: `npm run build` writes plain files to out/, which the FastAPI app serves (one image, one
// process). There is no Node server in production.
const config: NextConfig = {
  output: "export",
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  // During `npm run dev`, send /api and /health to the FastAPI app on :8000.
  ...(process.env.NODE_ENV === "development" && {
    async rewrites() {
      return [
        { source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" },
        { source: "/health", destination: "http://127.0.0.1:8000/health" },
      ];
    },
  }),
};

export default config;
