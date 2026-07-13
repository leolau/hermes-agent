/** @type {import('next').NextConfig} */
const nextConfig = {
  // The dashboard is served as a static SPA by the Python backend
  // (hermes_cli/web_server.py mount_spa), which injects auth/bootstrap
  // globals into index.html and falls back to index.html for client-side
  // routes. A static export keeps that contract: no Next.js server, no
  // Next route handlers — the Python `/api/*` layer stays authoritative.
  output: "export",
  reactStrictMode: true,
  // Emit hashed asset filenames without trailing-slash directory rewrites so
  // the exported index.html references /_next/... exactly as the SPA expects.
  trailingSlash: false,
  images: { unoptimized: true },
  // Workspace/source packages consumed as raw TS/ESM must be transpiled by
  // Next rather than assumed pre-built.
  transpilePackages: ["@hermes/shared", "@nous-research/ui"],
  // Lint and typecheck run as dedicated `npm run lint` / `npm run typecheck`
  // steps (and in CI); don't couple them to the production build.
  eslint: { ignoreDuringBuilds: true },
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.output.publicPath = "auto";
    }
    return config;
  },
};

export default nextConfig;
