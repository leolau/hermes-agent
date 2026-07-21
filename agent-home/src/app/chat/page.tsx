import { ComingSoon } from "@/components/ComingSoon";
import { MobileShell } from "@/components/MobileShell";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/** Placeholder tab — One-brain chat lands in Wave B2. */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="Chat">
      <ComingSoon wave="Wave B2" feature="One-brain chat" />
    </MobileShell>
  );
}
