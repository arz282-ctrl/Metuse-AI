import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pure static export for zero-cost Cloudflare Pages hosting (FastAPI owns all server logic)
  output: "export",
  images: {
    unoptimized: true,
  },
  reactStrictMode: true,
};

export default nextConfig;
