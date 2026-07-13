import { AppShell } from "@/app-shell";

// Optional catch-all so a single exported index.html backs every client-side
// route. The Python backend serves that index.html for any non-/api path
// (mount_spa), and react-router (inside AppShell) owns navigation at runtime.
export function generateStaticParams() {
  return [{ slug: [] }];
}

export default function Page() {
  return <AppShell />;
}
