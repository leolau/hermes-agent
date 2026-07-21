"use client";

import { useRef, useState } from "react";

import type { ChatAttachment } from "@/types";

/**
 * The message composer: an auto-clearing text field, an optional attach button
 * (present only when Storage is configured on the box), and a send button.
 * Uploads route through the `agent-home` BFF so the browser never holds a
 * storage key; attachment chips are included on the next send.
 */
export function Composer({
  sending,
  storageEnabled,
  sessionId,
  onSend,
}: {
  sending: boolean;
  storageEnabled: boolean;
  sessionId: string | null;
  onSend: (text: string, attachments: ChatAttachment[]) => void | Promise<void>;
}) {
  const [text, setText] = useState("");
  const [attachments, setAttachments] = useState<ChatAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const canSend = !sending && !uploading && (text.trim() !== "" || attachments.length > 0);

  function submit() {
    if (!canSend) return;
    void onSend(text.trim(), attachments);
    setText("");
    setAttachments([]);
  }

  async function upload(file: File) {
    setUploadError(null);
    setUploading(true);
    try {
      const form = new FormData();
      form.set("file", file);
      if (sessionId) form.set("sessionId", sessionId);
      const res = await fetch("/api/chat/upload", { method: "POST", body: form });
      const body = (await res.json()) as (ChatAttachment & { detail?: string });
      if (!res.ok) throw new Error(body.detail ?? "Upload failed.");
      setAttachments((prev) => [...prev, body]);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div
      data-component="Composer"
      className="sticky bottom-0 mt-3 bg-[var(--color-bg)] pt-2"
      style={{ bottom: "calc(var(--bottom-nav-h) + var(--safe-bottom))" }}
    >
      {attachments.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-2">
          {attachments.map((a) => (
            <span
              key={a.path}
              className="flex items-center gap-1 rounded-lg bg-[var(--color-surface-2)] px-2 py-1 text-xs"
            >
              {a.name}
              <button
                type="button"
                aria-label={`Remove ${a.name}`}
                onClick={() =>
                  setAttachments((prev) => prev.filter((x) => x.path !== a.path))
                }
                className="text-[var(--color-muted)]"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {uploadError ? (
        <p className="mb-2 text-xs text-red-300">{uploadError}</p>
      ) : null}
      <div className="flex items-end gap-2">
        {storageEnabled ? (
          <>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
              }}
            />
            <button
              type="button"
              aria-label="Attach image"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
              className="rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-3 text-sm disabled:opacity-60"
            >
              {uploading ? "…" : "+"}
            </button>
          </>
        ) : null}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="Message your agent…"
          className="max-h-32 min-h-[46px] flex-1 resize-none rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-3 text-sm"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="rounded-xl bg-[var(--color-accent)] px-4 py-3 text-sm font-semibold text-[var(--color-accent-fg)] disabled:opacity-60"
        >
          Send
        </button>
      </div>
    </div>
  );
}
