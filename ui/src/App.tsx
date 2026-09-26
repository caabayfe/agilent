import { useCallback, useEffect, useState } from "react";

import { api, ApiError } from "./api";
import { Chat } from "./components/Chat";
import { EvalsPanel } from "./components/EvalsPanel";
import { Header } from "./components/Header";
import { SkillsPanel } from "./components/SkillsPanel";
import { TracePanel } from "./components/TracePanel";
import { classes, newId } from "./format";
import { SUGGESTIONS } from "./suggestions";
import type {
  AuditEvent,
  ChatAnswer,
  EvalsView,
  GatewayInfo,
  Message,
  Persona,
  Session,
  SkillCatalog,
} from "./types";

type Tab = "trace" | "skills" | "evals";

const TAB_LABEL = new Map<Tab, string>([
  ["trace", "Trace & audit"],
  ["skills", "Skills"],
  ["evals", "Evals & promotion"],
]);

export function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  // The session token lives in memory only: never in localStorage or cookies.
  const [session, setSession] = useState<Session | null>(null);
  const [threadId, setThreadId] = useState(newId);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("trace");
  const [traceId, setTraceId] = useState<string | null>(null);
  const [trace, setTrace] = useState<AuditEvent[] | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [gateway, setGateway] = useState<GatewayInfo | null>(null);
  const [evals, setEvals] = useState<EvalsView | null>(null);
  const [skills, setSkills] = useState<SkillCatalog | null>(null);
  const [skillsError, setSkillsError] = useState<string | null>(null);
  const [fatal, setFatal] = useState<string | null>(null);

  const refreshGateway = useCallback(() => {
    api.gateway().then(setGateway, () => {
      setGateway(null);
    });
  }, []);
  const refreshEvals = useCallback(() => {
    api.evals().then(setEvals, () => {
      setEvals(null);
    });
  }, []);

  const signIn = useCallback(async (username: string) => {
    setSession(await api.login(username));
    setSkills(null);
    setMessages([]);
    setThreadId(newId());
    setTraceId(null);
    setTrace(null);
  }, []);

  useEffect(() => {
    api
      .personas()
      .then(async (list) => {
        setPersonas(list);
        if (list[0]) await signIn(list[0].username);
      })
      .catch((e: unknown) => {
        setFatal(e instanceof Error ? e.message : "API unavailable");
      });
    refreshGateway();
    refreshEvals();
    const timer = window.setInterval(() => {
      refreshGateway();
      refreshEvals();
    }, 10_000);
    return () => {
      window.clearInterval(timer);
    };
  }, [signIn, refreshGateway, refreshEvals]);

  useEffect(() => {
    if (tab !== "skills" || !session) return;
    api.skills(session.token).then(
      (catalog) => {
        setSkills(catalog);
        setSkillsError(null);
      },
      (e: unknown) => {
        setSkillsError(e instanceof Error ? e.message : "Could not load skills");
      },
    );
  }, [tab, session]);

  const selectTrace = useCallback(
    (id: string, token?: string) => {
      const bearer = token ?? session?.token;
      if (!bearer) return;
      setTab("trace");
      setTraceId(id);
      setTrace(null);
      setTraceError(null);
      api.audit(bearer, id).then(setTrace, (e: unknown) => {
        setTraceError(e instanceof Error ? e.message : "Could not load trace");
      });
    },
    [session],
  );

  const send = useCallback(
    async (text: string) => {
      if (!session) return;
      setMessages((m) => [...m, { id: newId(), role: "user", text }]);
      setBusy(true);
      try {
        let token = session.token;
        let answer: ChatAnswer;
        try {
          answer = await api.chat(token, text, threadId);
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 401)) throw e;
          // Mock SSO: an IdP restart rotates its signing key and invalidates the session.
          // Sign in again silently (the equivalent of a token refresh) and retry once.
          const fresh = await api.login(session.user.sub);
          setSession(fresh);
          token = fresh.token;
          answer = await api.chat(token, text, threadId);
        }
        setMessages((m) => [...m, { id: newId(), role: "assistant", text: answer.answer, meta: answer }]);
        selectTrace(answer.trace_id, token);
      } catch (e) {
        const message =
          e instanceof ApiError && e.status === 401
            ? "Your session expired. Switch persona to sign in again."
            : e instanceof Error
              ? e.message
              : "Something went wrong.";
        setMessages((m) => [...m, { id: newId(), role: "error", text: message }]);
      } finally {
        setBusy(false);
        refreshGateway();
      }
    },
    [session, threadId, selectTrace, refreshGateway],
  );

  if (fatal) {
    return (
      <main className="grid h-screen place-items-center p-6 text-center">
        <div>
          <p className="font-semibold text-slate-900">The assistant backend is not reachable.</p>
          <p className="mt-1 text-sm text-slate-500">{fatal}. Is the stack up? Try `make up`.</p>
        </div>
      </main>
    );
  }

  return (
    <div className="flex h-screen flex-col">
      <Header
        personas={personas}
        session={session}
        gateway={gateway}
        onSwitch={(u) => {
          void signIn(u);
        }}
      />
      <main className="flex min-h-0 flex-1">
        <Chat
          messages={messages}
          suggestions={SUGGESTIONS[session?.user.sub ?? ""] ?? []}
          busy={busy}
          selectedTrace={traceId}
          onSend={(t) => {
            void send(t);
          }}
          onSelectTrace={selectTrace}
        />
        <aside
          className="flex w-[30rem] shrink-0 flex-col border-l border-slate-200 bg-slate-50/60"
          aria-label="Details"
        >
          <div className="flex border-b border-slate-200 bg-white px-3" role="tablist">
            {(["trace", "skills", "evals"] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                onClick={() => {
                  setTab(t);
                }}
                className={classes(
                  "-mb-px border-b-2 px-3 py-2.5 text-sm font-medium",
                  tab === t
                    ? "border-indigo-600 text-indigo-700"
                    : "border-transparent text-slate-500 hover:text-slate-800",
                )}
              >
                {TAB_LABEL.get(t)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4" role="tabpanel">
            {tab === "trace" && <TracePanel traceId={traceId} events={trace} error={traceError} />}
            {tab === "skills" && (
              <SkillsPanel
                catalog={skills}
                error={skillsError}
                persona={session?.user.name}
                busy={busy}
                onAsk={(q) => {
                  void send(q);
                }}
              />
            )}
            {tab === "evals" && <EvalsPanel view={evals} onRefresh={refreshEvals} />}
          </div>
        </aside>
      </main>
    </div>
  );
}
