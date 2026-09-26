import { useEffect, useRef, useState } from "react";

import { classes, ms } from "../format";
import type { Suggestion } from "../suggestions";
import type { Message } from "../types";
import { Badge } from "./Badge";

interface Props {
  messages: Message[];
  suggestions: Suggestion[];
  busy: boolean;
  selectedTrace: string | null;
  onSend: (text: string) => void;
  onSelectTrace: (traceId: string) => void;
}

export function Chat({ messages, suggestions, busy, selectedTrace, onSend, onSelectTrace }: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    onSend(trimmed);
    setDraft("");
  };

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversation">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-6">
        {messages.length === 0 && (
          <div className="mx-auto max-w-xl pt-10 text-center">
            <h1 className="text-lg font-semibold text-slate-900">How can we help today?</h1>
            <p className="mt-1 text-sm text-slate-500">
              Ask about orders, invoices, your instruments' service history, or troubleshooting.
            </p>
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            selected={m.role === "assistant" && m.meta.trace_id === selectedTrace}
            onSelectTrace={onSelectTrace}
          />
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
            <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" /> Thinking…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-slate-200 bg-white px-6 pt-3 pb-4">
        <div className="mb-3 flex flex-wrap gap-2">
          {suggestions.map((s) => (
            <button
              key={s.text}
              type="button"
              disabled={busy}
              onClick={() => {
                submit(s.text);
              }}
              className="group rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-left text-xs text-slate-700 transition hover:border-indigo-300 hover:bg-indigo-50 disabled:opacity-50"
            >
              {s.text.length > 60 ? `${s.text.slice(0, 57)}…` : s.text}
              <span className="ml-1.5 text-slate-400 group-hover:text-indigo-500">· {s.hint}</span>
            </button>
          ))}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit(draft);
          }}
        >
          <label htmlFor="question" className="sr-only">
            Your question
          </label>
          <input
            id="question"
            value={draft}
            maxLength={2000}
            autoComplete="off"
            placeholder="Ask about an order, invoice, instrument or error code…"
            onChange={(e) => {
              setDraft(e.target.value);
            }}
            className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || !draft.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            Send
          </button>
        </form>
      </div>
    </section>
  );
}

function MessageBubble({
  message,
  selected,
  onSelectTrace,
}: {
  message: Message;
  selected: boolean;
  onSelectTrace: (traceId: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <p className="max-w-[75%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-sm whitespace-pre-wrap text-white">
          {message.text}
        </p>
      </div>
    );
  }
  if (message.role === "error") {
    return (
      <p
        className="max-w-[75%] rounded-xl border border-rose-200 bg-rose-50 px-4 py-2 text-sm text-rose-800"
        role="alert"
      >
        {message.text}
      </p>
    );
  }
  const { meta } = message;
  return (
    <div className="max-w-[80%]">
      <button
        type="button"
        onClick={() => {
          onSelectTrace(meta.trace_id);
        }}
        aria-pressed={selected}
        className={classes(
          "w-full rounded-2xl rounded-bl-sm border bg-white px-4 py-3 text-left shadow-sm transition",
          selected ? "border-indigo-400 ring-2 ring-indigo-100" : "border-slate-200 hover:border-slate-300",
        )}
      >
        {/* Model output is rendered as plain text, never as HTML. */}
        <p className="text-sm whitespace-pre-wrap text-slate-800">{message.text}</p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2">
          <Badge tone="slate">intent: {meta.intent ?? "?"}</Badge>
          <Badge tone="indigo">routed → {meta.model_alias ?? "?"}</Badge>
          <Badge tone={meta.model_resolved?.includes("failover") ? "amber" : "green"}>
            answered by {meta.model_resolved ?? "?"}
          </Badge>
          <span className="ml-auto text-[11px] text-slate-400">{ms(meta.latency_ms)} · view trace →</span>
        </div>
      </button>
    </div>
  );
}
