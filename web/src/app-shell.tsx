"use client";

import dynamic from "next/dynamic";

// The dashboard is a client-only SPA (react-router, window/localStorage,
// server-injected bootstrap globals). Loading it with ssr:false keeps Next's
// static export from executing browser-only code during prerender while still
// producing the index.html shell the Python backend serves.
const AppRoot = dynamic(() => import("@/AppRoot"), { ssr: false });

export function AppShell() {
  return <AppRoot />;
}
