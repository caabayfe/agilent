import { useState } from "react";

import type { GatewayInfo, Persona, Session } from "../types";
import { Badge } from "./Badge";
import { ModelSelector } from "./ModelSelector";

interface Props {
  personas: Persona[];
  session: Session | null;
  gateway: GatewayInfo | null;
  onSwitch: (username: string) => void;
  onGatewayChanged: () => void;
  onOpenMatrix: () => void;
}

export function Header({ personas, session, gateway, onSwitch, onGatewayChanged, onOpenMatrix }: Props) {
  return (
    <header className="flex flex-wrap items-center gap-4 border-b border-slate-200 bg-white px-5 py-3">
      <div className="flex items-center gap-2.5">
        <img src="/favicon.svg" alt="" className="h-8 w-8" />
        <div>
          <p className="text-sm font-semibold text-slate-900">Helix Instruments</p>
          <p className="text-xs text-slate-500">Customer Assistant · agent-plane PoC</p>
        </div>
      </div>

      <GatewayBadge gateway={gateway} token={session?.token ?? null} onChanged={onGatewayChanged} />

      <button
        type="button"
        onClick={onOpenMatrix}
        className="rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
        title="Same eval suite on every model combination"
      >
        Model matrix
      </button>

      <div className="ml-auto flex items-center gap-3">
        {session && (
          <div className="hidden text-right sm:block">
            <p className="text-sm font-medium text-slate-900">{session.user.name}</p>
            <p className="text-xs text-slate-500">
              {session.user.customer_name} · {session.user.roles.join(", ")}
            </p>
          </div>
        )}
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Signed in as
          <select
            className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            value={session?.user.sub ?? ""}
            onChange={(e) => {
              onSwitch(e.target.value);
            }}
          >
            {personas.map((p) => (
              <option key={p.username} value={p.username}>
                {p.display_name} ({p.customer_id})
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}

function GatewayBadge({
  gateway,
  token,
  onChanged,
}: {
  gateway: GatewayInfo | null;
  token: string | null;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!gateway) return null;
  const aliases = Object.entries(gateway.aliases).filter(([alias]) => alias.startsWith("assistant-"));
  const tone = gateway.profile.includes("broken")
    ? "red"
    : gateway.profile.includes("swapped") || gateway.profile === "custom"
      ? "amber"
      : "indigo";
  const canManage = Boolean(gateway.admin_enabled && token);
  return (
    <div
      className="relative flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5"
      title="Model gateway: aliases the agent calls, and the deployment behind each one"
    >
      <span className="text-[11px] font-semibold tracking-wide text-slate-500 uppercase">Gateway</span>
      <Badge tone={tone}>{gateway.profile}</Badge>
      {aliases.map(([alias, deployments]) => (
        <span key={alias} className="text-xs text-slate-600">
          <span className="font-medium text-slate-800">{alias}</span> →{" "}
          {deployments.map((d) => d.model.replace("os.environ/", "$")).join(", ")}
        </span>
      ))}
      {canManage && (
        <button
          type="button"
          aria-expanded={open}
          onClick={() => {
            setOpen((o) => !o);
          }}
          className="rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs font-medium text-slate-700 hover:bg-slate-100"
        >
          Change models ▾
        </button>
      )}
      {canManage && open && token && (
        <ModelSelector
          token={token}
          onChanged={onChanged}
          onClose={() => {
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}
