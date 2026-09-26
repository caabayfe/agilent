import type { AuditEvent, ChatAnswer, EvalsView, GatewayInfo, Persona, Session, User } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // non-JSON error body: keep the status text
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export const api = {
  personas: () => request<Persona[]>("/api/personas"),
  login: async (username: string): Promise<Session> => {
    const body = await request<{ access_token: string; user: User }>("/api/login", {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    return { token: body.access_token, user: body.user };
  },
  chat: (token: string, message: string, threadId: string) =>
    request<ChatAnswer>(
      "/api/chat",
      { method: "POST", body: JSON.stringify({ message, thread_id: threadId }) },
      token,
    ),
  audit: (token: string, traceId: string) =>
    request<AuditEvent[]>(`/api/audit/${encodeURIComponent(traceId)}`, {}, token),
  gateway: () => request<GatewayInfo>("/api/gateway"),
  evals: () => request<EvalsView>("/api/evals/latest"),
};
