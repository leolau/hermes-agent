import { Pill } from "@/components/ui/Pill";
import type { Tool, ToolsResponse } from "@/types";

/**
 * FG-20 Wave B3 — mobile-first tool registry (read-only, C2-scoped).
 *
 * The mobile face of `web/`'s `ToolsPage`: it lists the FG-07 tools the Python
 * registry returns from `/api/tools` for a datastore mode — name, kind, stack,
 * visibility, and enable status. **Read-only**: enable/config/promote stay on
 * the operator authority paths (with C5 provenance), so this surface never
 * mutates the registry. The browser only ever gets already-scoped rows.
 */

function ToolRow({ tool }: { tool: Tool }) {
  return (
    <li data-component="ToolRow">
      <div className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{tool.name}</span>
          <Pill tone={tool.enabled ? "success" : "muted"}>
            {tool.enabled ? "enabled" : "disabled"}
          </Pill>
          <Pill tone="muted">{tool.kind}</Pill>
        </div>
        <p className="mt-1 text-xs text-[var(--color-muted)]">
          {tool.stack} · {tool.visibility}
          {tool.mcp_endpoint_ref ? ` · ${tool.mcp_endpoint_ref}` : ""}
        </p>
      </div>
    </li>
  );
}

export function ToolsRegistry({ data }: { data: ToolsResponse }) {
  if (!data.configured) {
    return (
      <div data-component="ToolsRegistry" className="text-sm text-[var(--color-muted)]">
        Tool registry not configured (needs the application datastore).
      </div>
    );
  }

  const enabled = data.tools.filter((t) => t.enabled);
  const disabled = data.tools.filter((t) => !t.enabled);

  return (
    <div data-component="ToolsRegistry" className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Pill tone="accent">{data.mode}</Pill>
        <span className="text-sm text-[var(--color-muted)]">
          {data.tools.length} tools
        </span>
      </div>

      {data.tools.length === 0 ? (
        <p className="text-sm text-[var(--color-muted)]">
          No tools registered in this scope yet.
        </p>
      ) : (
        <>
          <section>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Enabled ({enabled.length})
            </h2>
            {enabled.length === 0 ? (
              <p className="text-sm text-[var(--color-muted)]">None enabled.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {enabled.map((tool) => (
                  <ToolRow key={tool.id} tool={tool} />
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-muted)]">
              Disabled ({disabled.length})
            </h2>
            {disabled.length === 0 ? (
              <p className="text-sm text-[var(--color-muted)]">None disabled.</p>
            ) : (
              <ul className="flex flex-col gap-2">
                {disabled.map((tool) => (
                  <ToolRow key={tool.id} tool={tool} />
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      <p className="text-xs text-[var(--color-muted)]">
        Read-only · enable/config/promote run on the operator authority paths.
      </p>
    </div>
  );
}
