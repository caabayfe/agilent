import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api";
import type { CatalogModel, GatewayCatalog } from "../types";
import { ModelSelector } from "./ModelSelector";

const model = (id: string, vendor: string, missing: string[] = []): CatalogModel => ({
  id,
  label: id,
  vendor,
  notes: "",
  resolves_to: `azure/${id}`,
  api_base_host: "example.openai.azure.com",
  available: missing.length === 0,
  missing,
});

const catalog = (fast: string, reasoning: string, profile = "live"): GatewayCatalog => ({
  profile,
  aliases: ["assistant-fast", "assistant-reasoning"],
  models: [
    model("gpt-4.1-mini", "Azure OpenAI"),
    model("gpt-5.4", "Azure OpenAI"),
    model("alt", "other", ["MODEL_ALT_API_KEY"]),
  ],
  selection: { "assistant-fast": fast, "assistant-reasoning": reasoning },
  profiles: [
    { name: "live", missing: [] },
    { name: "live-alt", missing: ["MODEL_ALT_API_KEY"] },
    { name: "live-swapped", missing: [] },
  ],
  busy: false,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ModelSelector", () => {
  it("shows the current selection and disables models that are not configured", async () => {
    vi.spyOn(api, "gatewayCatalog").mockResolvedValue(catalog("gpt-4.1-mini", "gpt-5.4"));
    render(<ModelSelector token="t" onChanged={vi.fn()} onClose={vi.fn()} />);
    const fast = await screen.findByRole("combobox", { name: "assistant-fast" });
    expect(fast).toHaveValue("gpt-4.1-mini");
    expect(screen.getAllByRole("option", { name: "alt (set MODEL_ALT_API_KEY)" })[0]).toBeDisabled();
    expect(screen.getByRole("option", { name: "live-alt (set MODEL_ALT_API_KEY)" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Apply models" })).toBeDisabled();
  });

  it("applies a per-alias selection and notifies the app", async () => {
    vi.spyOn(api, "gatewayCatalog").mockResolvedValue(catalog("gpt-4.1-mini", "gpt-5.4"));
    const select = vi.spyOn(api, "selectModels").mockResolvedValue(catalog("gpt-5.4", "gpt-5.4", "custom"));
    const onChanged = vi.fn();
    render(<ModelSelector token="t" onChanged={onChanged} onClose={vi.fn()} />);
    fireEvent.change(await screen.findByRole("combobox", { name: "assistant-fast" }), {
      target: { value: "gpt-5.4" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply models" }));
    expect(select).toHaveBeenCalledWith("t", {
      selection: { "assistant-fast": "gpt-5.4", "assistant-reasoning": "gpt-5.4" },
    });
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it("loads a named profile and shows gateway errors", async () => {
    vi.spyOn(api, "gatewayCatalog").mockResolvedValue(catalog("gpt-4.1-mini", "gpt-5.4"));
    const select = vi.spyOn(api, "selectModels").mockRejectedValue(new Error("rolled back to 'live'"));
    render(<ModelSelector token="t" onChanged={vi.fn()} onClose={vi.fn()} />);
    fireEvent.change(await screen.findByRole("combobox", { name: "Or load a named profile" }), {
      target: { value: "live-swapped" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Load" }));
    expect(select).toHaveBeenCalledWith("t", { profile: "live-swapped" });
    expect(await screen.findByRole("alert")).toHaveTextContent("rolled back to 'live'");
  });
});
