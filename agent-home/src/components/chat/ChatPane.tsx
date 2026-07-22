"use client";

import { useEffect, useRef, useState } from "react";

import { ConversationList } from "@/components/chat/ConversationList";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { Composer } from "@/components/chat/Composer";
import type {
  ChatAttachment,
  ChatMessage,
  ChatSendResponse,
  SessionSummary,
} from "@/types";

export interface ChatPaneProps {
  initialSessions: SessionSummary[];
  initialSessionId: string | null;
  initialMessages: ChatMessage[];
  storageEnabled: boolean;
}

/** Only user/assistant turns are shown in the visible thread. */
function visible(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m) => m.role === "user" || m.role === "assistant");
}

/**
 * FG-20 Wave C1 — the mobile-first one-brain chat pane. A conversation switcher
 * (sheet), a scrollable message thread, and a composer that sends one turn
 * through the `agent-home` BFF (`/api/chat/*`) to the principal-scoped Python
 * endpoint. It never talks to the AI layer or the model loop directly.
 */
export function ChatPane({
  initialSessions,
  initialSessionId,
  initialMessages,
  storageEnabled,
}: ChatPaneProps) {
  const [sessions, setSessions] = useState<SessionSummary[]>(initialSessions);
  const [sessionId, setSessionId] = useState<string | null>(initialSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(visible(initialMessages));
  const [listOpen, setListOpen] = useState(false);
  const [loadingThread, setLoadingThread] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, sending]);

  async function openConversation(id: string) {
    setListOpen(false);
    if (id === sessionId) return;
    setSessionId(id);
    setError(null);
    setLoadingThread(true);
    setMessages([]);
    try {
      const res = await fetch(
        `/api/chat/messages?sessionId=${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as {
        messages?: ChatMessage[];
        detail?: string;
      };
      if (!res.ok) throw new Error(body.detail ?? "Failed to load conversation.");
      setMessages(visible(body.messages ?? []));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load conversation.");
    } finally {
      setLoadingThread(false);
    }
  }

  function startNewConversation() {
    setListOpen(false);
    setSessionId(null);
    setMessages([]);
    setError(null);
  }

  async function send(text: string, attachments: ChatAttachment[]) {
    if (sending) return;
    setError(null);
    setSending(true);
    const optimistic: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, optimistic]);
    try {
      const res = await fetch("/api/chat/send", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ sessionId, message: text, attachments }),
      });
      const body = (await res.json()) as (ChatSendResponse & { detail?: string });
      if (!res.ok) throw new Error(body.detail ?? "The message could not be sent.");
      setSessionId(body.session_id);
      setMessages((prev) => [...prev, body.message]);
      void refreshSessions();
    } catch (err) {
      setMessages((prev) => prev.filter((m) => m !== optimistic));
      setError(err instanceof Error ? err.message : "The message could not be sent.");
    } finally {
      setSending(false);
    }
  }

  async function refreshSessions() {
    try {
      const res = await fetch("/api/chat/sessions", { cache: "no-store" });
      if (!res.ok) return;
      const body = (await res.json()) as { sessions?: SessionSummary[] };
      if (body.sessions) setSessions(body.sessions);
    } catch {
      // A stale conversation list is non-fatal.
    }
  }

  const activeTitle =
    sessions.find((s) => s.id === sessionId)?.title || "New conversation";

  return (
    <div data-component="ChatPane" className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setListOpen(true)}
          className="flex-1 truncate rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-left text-sm"
        >
          <span className="text-[var(--color-muted)]">Conversation · </span>
          {activeTitle}
        </button>
        <button
          type="button"
          onClick={startNewConversation}
          className="rounded-xl bg-[var(--color-accent)] px-3 py-2 text-sm font-semibold text-[var(--color-accent-fg)]"
        >
          New
        </button>
      </div>

      <div
        ref={threadRef}
        className="min-h-[42dvh] max-h-[calc(100dvh-19rem)] flex-1 space-y-3 overflow-y-auto rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-3"
      >
        {loadingThread ? (
          <p className="py-8 text-center text-sm text-[var(--color-muted)]">
            Loading conversation…
          </p>
        ) : messages.length === 0 ? (
          <p className="py-8 text-center text-sm text-[var(--color-muted)]">
            {sessionId
              ? "No messages yet — say hello."
              : "Start a new conversation with your agent."}
          </p>
        ) : (
          messages.map((m, i) => <MessageBubble key={m.id ?? i} message={m} />)
        )}
        {sending ? (
          <div className="flex justify-start">
            <span className="rounded-2xl bg-[var(--color-surface-2)] px-3 py-2 text-sm text-[var(--color-muted)]">
              Thinking…
            </span>
          </div>
        ) : null}
      </div>

      {error ? (
        <p className="mt-2 rounded-lg bg-[var(--color-surface-2)] px-3 py-2 text-sm text-red-300">
          {error}
        </p>
      ) : null}

      <Composer sending={sending} storageEnabled={storageEnabled} sessionId={sessionId} onSend={send} />

      {listOpen ? (
        <ConversationList
          sessions={sessions}
          activeId={sessionId}
          onSelect={openConversation}
          onClose={() => setListOpen(false)}
        />
      ) : null}
    </div>
  );
}
