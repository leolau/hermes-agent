/** @type {import('next').NextConfig} */
const nextConfig = {
  // Unlike `web/` (a static SPA export served by the Python backend),
  // `agent-home` runs as a real Next.js **server** (App Router + route
  // handlers) behind Caddy on the prod box. It is the BFF (FG-20 Decision 1):
  // it holds the C1 principal session, proxies agent/authority calls to the
  // Python `/api/*` layer, and does server-side Supabase reads with the
  // principal's RLS context. So there is deliberately NO `output: "export"`.
  reactStrictMode: true,
  // Lint and typecheck run as dedicated `npm run lint` / `npm run typecheck`
  // steps (and in CI); don't couple them to the production build.
  eslint: { ignoreDuringBuilds: true },
  // `pg` is a server-only dependency; never bundle it into client chunks.
  serverExternalPackages: ["pg"],
};

export default nextConfig;
