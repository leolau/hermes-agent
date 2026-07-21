import { ComingSoon } from "@/components/ComingSoon";
import { MobileShell } from "@/components/MobileShell";
import { requirePrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/** Placeholder tab — Goal / Task / Skill graph lands in Wave B1. */
export default async function Page() {
  await requirePrincipal();
  return (
    <MobileShell title="GTS Graph">
      <ComingSoon wave="Wave B1" feature="Goal / Task / Skill graph" />
    </MobileShell>
  );
}
