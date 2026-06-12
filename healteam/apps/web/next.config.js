/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Required by apps/web/Dockerfile: builds `.next/standalone/server.js` so the
  // slim runner image can `CMD ["node", "server.js"]` without bundling
  // node_modules. See docs/diagnosis-2026-06-04.md §4.3.
  output: "standalone",
  // W1: API base URL is the FastAPI service in docker-compose.
  // W2: switch to WebSocket endpoint for live therapy streaming.
  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  },
};

module.exports = nextConfig;
