import { ComingSoon } from "@/components/ComingSoon";
import { MobileShell } from "@/components/MobileShell";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/** Placeholder tab — Interaction trace & notifications lands in Wave C2. */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="Activity">
      <ComingSoon wave="Wave C2" feature="Interaction trace & notifications" />
    </MobileShell>
  );
}
