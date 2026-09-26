import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Skill, SkillCatalog } from "../types";
import { SkillsPanel } from "./SkillsPanel";

const skill = (name: string, tier: number, access: Skill["access"]): Skill => ({
  name,
  title: `${name} title`,
  description: `${name} description`,
  component: `mcp-customer/${name}`,
  risk_tier: tier,
  required_agent_scope: `${name}.read`,
  required_user_role: `${name}:view`,
  examples: [`Ask ${name} something`],
  enabled: access.reason !== "skill_disabled",
  access,
  tools: [
    { name: `list_${name}`, description: "Lists things.", input_schema: { properties: { only_open: {} } } },
  ],
});

const catalog: SkillCatalog = {
  server: "mcp-customer",
  skills: [
    skill("orders", 1, { allowed: true, reason: "entitled" }),
    skill("billing", 2, { allowed: false, reason: "user_role_missing" }),
    skill("service", 1, { allowed: false, reason: "skill_disabled" }),
  ],
};

describe("SkillsPanel", () => {
  it("shows every skill of the one server with its tier, tools and the persona's access", () => {
    render(<SkillsPanel catalog={catalog} error={null} persona="bob" busy={false} onAsk={vi.fn()} />);
    expect(screen.getByText("3 skills")).toBeInTheDocument();
    expect(screen.getByText("tier 2")).toBeInTheDocument();
    expect(screen.getByText("list_billing(only_open)")).toBeInTheDocument();
    expect(screen.getByText("denied · user lacks role")).toBeInTheDocument();
    expect(screen.getByText("denied · switched off")).toBeInTheDocument();
  });

  it("sends an example question to the chat", () => {
    const onAsk = vi.fn();
    render(<SkillsPanel catalog={catalog} error={null} persona="alice" busy={false} onAsk={onAsk} />);
    fireEvent.click(screen.getByRole("button", { name: "Ask: Ask orders something" }));
    expect(onAsk).toHaveBeenCalledWith("Ask orders something");
  });
});
