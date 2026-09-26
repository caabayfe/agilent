import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Chat } from "./Chat";

describe("Chat", () => {
  it("sends a suggested question and shows routing metadata on answers", async () => {
    const onSend = vi.fn();
    render(
      <Chat
        busy={false}
        selectedTrace={null}
        onSelectTrace={vi.fn()}
        onSend={onSend}
        suggestions={[{ text: "Show me invoice INV-5202.", hint: "cross-tenant" }]}
        messages={[
          {
            id: "a",
            role: "assistant",
            text: "Invoice INV-5202 was not found for your account.",
            meta: {
              trace_id: "t",
              answer: "",
              intent: "billing",
              model_alias: "assistant-fast",
              model_resolved: "azure/gpt-5-mini",
              latency_ms: 900,
            },
          },
        ]}
      />,
    );
    expect(screen.getByText("routed → assistant-fast")).toBeInTheDocument();
    expect(screen.getByText("answered by azure/gpt-5-mini")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Show me invoice INV-5202/ }));
    expect(onSend).toHaveBeenCalledWith("Show me invoice INV-5202.");
  });
});
