import { useEffect, useState } from "react";

import { api } from "../api";
import type { CatalogModel, GatewayCatalog, GatewayChange } from "../types";

interface Props {
  token: string;
  onChanged: () => void;
  onClose: () => void;
}

const SELECT =
  "w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 focus:ring-2 focus:ring-indigo-500 focus:outline-none disabled:bg-slate-100";

function optionLabel(m: CatalogModel): string {
  if (!m.available) return `${m.label} (set ${m.missing.join(", ")})`;
  const resolved = m.resolves_to.replace(/^[a-z_]+\//, "");
  return resolved && resolved !== m.label ? `${m.label} → ${resolved}` : m.label;
}

function byVendor(models: CatalogModel[]): [string, CatalogModel[]][] {
  const groups = new Map<string, CatalogModel[]>();
  for (const m of models) groups.set(m.vendor, [...(groups.get(m.vendor) ?? []), m]);
  return [...groups.entries()];
}

/** Operator control (local demo): re-point each gateway alias at a model from the catalog. */
export function ModelSelector({ token, onChanged, onClose }: Props) {
  const [catalog, setCatalog] = useState<GatewayCatalog | null>(null);
  const [draft, setDraft] = useState<Map<string, string>>(new Map());
  const [preset, setPreset] = useState("");
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = (c: GatewayCatalog) => {
    setCatalog(c);
    setDraft(new Map(Object.entries(c.selection).flatMap(([alias, id]) => (id ? [[alias, id]] : []))));
    setPreset(c.profile);
  };

  useEffect(() => {
    api.gatewayCatalog(token).then(load, (e: unknown) => {
      setError(e instanceof Error ? e.message : "Could not load the model catalog");
    });
  }, [token]);

  const apply = (change: GatewayChange) => {
    setApplying(true);
    setError(null);
    api
      .selectModels(token, change)
      .then(load, (e: unknown) => {
        setError(e instanceof Error ? e.message : "The gateway change failed");
      })
      .finally(() => {
        setApplying(false);
        onChanged();
      });
  };

  const current = new Map(Object.entries(catalog?.selection ?? {}));
  const complete = catalog?.aliases.every((a) => draft.has(a)) ?? false;
  const dirty = catalog?.aliases.some((a) => draft.get(a) !== current.get(a)) ?? false;

  return (
    <div
      role="dialog"
      aria-label="Model selector"
      className="absolute top-full left-0 z-20 mt-2 w-[26rem] rounded-lg border border-slate-200 bg-white p-4 text-left shadow-lg"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-slate-900">Models behind each alias</p>
          <p className="text-xs text-slate-500">
            The agent only calls the aliases. Changing what they point at is a gateway config change; the eval
            certificate goes STALE unless the result is a profile that was evaluated.
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded px-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          ×
        </button>
      </div>

      {!catalog && !error && <p className="text-sm text-slate-500">Loading catalog…</p>}

      {catalog && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            apply({ selection: Object.fromEntries(draft) });
          }}
          className="space-y-3"
        >
          {catalog.aliases.map((alias) => (
            <div key={alias}>
              <label htmlFor={`model-${alias}`} className="block text-xs font-medium text-slate-700">
                {alias}
              </label>
              <select
                id={`model-${alias}`}
                className={`${SELECT} mt-1`}
                value={draft.get(alias) ?? ""}
                disabled={applying}
                onChange={(e) => {
                  setDraft((d) => new Map(d).set(alias, e.target.value));
                }}
              >
                {!current.get(alias) && (
                  <option value="" disabled>
                    (not a catalog model: profile {catalog.profile})
                  </option>
                )}
                {byVendor(catalog.models).map(([vendor, models]) => (
                  <optgroup key={vendor} label={vendor}>
                    {models.map((m) => (
                      <option key={m.id} value={m.id} disabled={!m.available} title={m.notes}>
                        {optionLabel(m)}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </div>
          ))}
          <button
            type="submit"
            disabled={applying || !complete || !dirty}
            className="w-full rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
          >
            {applying ? "Restarting gateway…" : "Apply models"}
          </button>

          <div className="border-t border-slate-100 pt-3">
            <label htmlFor="model-profile" className="block text-xs font-medium text-slate-700">
              Or load a named profile
            </label>
            <div className="mt-1 flex gap-2">
              <select
                id="model-profile"
                className={SELECT}
                value={preset}
                disabled={applying}
                onChange={(e) => {
                  setPreset(e.target.value);
                }}
              >
                {catalog.profile === "custom" && <option value="custom">custom (current)</option>}
                {catalog.profiles.map((p) => (
                  <option key={p.name} value={p.name} disabled={p.missing.length > 0}>
                    {p.name}
                    {p.missing.length > 0 ? ` (set ${p.missing.join(", ")})` : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                disabled={applying || preset === catalog.profile || preset === "custom"}
                onClick={() => {
                  apply({ profile: preset });
                }}
                className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-40"
              >
                Load
              </button>
            </div>
          </div>
        </form>
      )}

      {error && (
        <p role="alert" className="mt-3 rounded-md bg-rose-50 px-2 py-1.5 text-xs text-rose-700">
          {error}
        </p>
      )}
    </div>
  );
}
