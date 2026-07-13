import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, MessageCircle, Radio, Send } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import { resolveTelegramChannel } from "@/lib/telegram";
import type { TelegramChannelView } from "@/lib/telegram";

/**
 * FG-17b Embedded Telegram pane (D13). Telegram is both a native channel (the
 * app) and a dashboard-embedded conversational surface — **both hit the same
 * FG-03 one-brain gateway/session backend**. Per the FG-17 doc's recorded
 * fallback (official web-widget embedding is blocked by Telegram's
 * auth/security constraints), the dashboard-embedded surface is the existing
 * one-brain chat pane (``/chat`` → TUI PTY → ``tui_gateway`` → ``AIAgent``),
 * which is the same backend the Telegram app talks to. This pane surfaces the
 * live Telegram channel status and routes into that shared backend.
 */
export default function TelegramPage() {
  const [view, setView] = useState<TelegramChannelView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { setEnd } = usePageHeader();
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.getMessagingPlatforms();
      setView(resolveTelegramChannel(resp.platforms));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load Telegram");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setEnd(<Badge tone="secondary">one-brain backend (FG-03)</Badge>);
    return () => setEnd(null);
  }, [setEnd]);

  if (loading) {
    return (
      <div
        data-component="TelegramPage"
        className="flex items-center gap-2 text-muted-foreground"
      >
        <Spinner /> Loading Telegram channel…
      </div>
    );
  }

  return (
    <div data-component="TelegramPage" className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <H2>Telegram</H2>
      </div>

      {error ? (
        <Card>
          <CardContent className="p-5 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      ) : null}

      {/* Channel status (native app surface of the one-brain backend) */}
      <Card>
        <CardContent className="flex flex-col gap-3 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <Radio className="h-5 w-5 text-muted-foreground" />
            <span className="font-medium">Telegram channel</span>
            <Badge tone={view?.connected ? "success" : "warning"}>
              {view?.status ?? "unavailable"}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            Telegram is a native channel <em>and</em> a dashboard surface — both
            route through the same one-brain gateway (FG-03), so a conversation
            continues seamlessly across the app and the dashboard.
          </p>
          {view?.platform && !view.connected ? (
            <Button
              ghost
              className="w-fit uppercase"
              onClick={() => navigate("/channels")}
            >
              <Radio className="mr-1 h-4 w-4" /> Configure in Channels
            </Button>
          ) : null}
          {view?.platform?.docs_url ? (
            <a
              className="inline-flex w-fit items-center gap-1 text-sm text-primary hover:underline"
              href={view.platform.docs_url}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink className="h-3 w-3" /> Telegram setup docs
            </a>
          ) : null}
        </CardContent>
      </Card>

      {/* Dashboard-embedded conversational surface (same backend session) */}
      <Card>
        <CardContent className="flex flex-col gap-3 p-5">
          <div className="flex items-center gap-2">
            <MessageCircle className="h-5 w-5 text-muted-foreground" />
            <span className="font-medium">Chat in the dashboard</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Talk to the same agent from here. The in-dashboard chat is bound to
            the same one-brain backend as the Telegram app, so memory, skills,
            and context are shared.
          </p>
          <Button className="w-fit uppercase" onClick={() => navigate("/chat")}>
            <Send className="mr-1 h-4 w-4" /> Open dashboard chat
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
