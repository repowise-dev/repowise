import { useState } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatComposer, type ChatComposerProps } from "../../src/chat/chat-composer.js";

function Controlled(props: Partial<ChatComposerProps> & { initial?: string }) {
  const { initial = "", ...rest } = props;
  const [value, setValue] = useState(initial);
  return (
    <ChatComposer
      value={value}
      onValueChange={setValue}
      onSend={vi.fn()}
      onCancel={vi.fn()}
      isStreaming={false}
      placeholder="Ask"
      {...rest}
    />
  );
}

describe("ChatComposer", () => {
  it("sends on Enter and keeps Shift+Enter for a newline", () => {
    const onSend = vi.fn();
    render(<Controlled initial="hello" onSend={onSend} />);
    const input = screen.getByLabelText("Chat message");
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(input).toHaveValue("");
  });

  it("does not send the Enter that confirms an IME candidate", () => {
    const onSend = vi.fn();
    render(<Controlled initial="nihao" onSend={onSend} />);
    const input = screen.getByLabelText("Chat message");
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.compositionEnd(input);
    // Safari: the confirming Enter arrives after compositionend as keyCode 229.
    fireEvent.keyDown(input, { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onSend).toHaveBeenCalledWith("nihao");
  });

  it("puts the question back when the send fails", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("offline"));
    render(<Controlled initial="why is this slow?" onSend={onSend} />);
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Chat message")).toHaveValue("why is this slow?"),
    );
  });

  it("keeps Stop enabled while streaming and tells it apart from Send", () => {
    const onCancel = vi.fn();
    const { container } = render(<Controlled isStreaming onCancel={onCancel} />);
    const stop = screen.getByRole("button", { name: "Stop generation" });
    expect(stop).toBeEnabled();
    expect(container.querySelector("[data-chat-composer]")).toHaveAttribute("data-streaming", "true");
    fireEvent.click(stop);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("ties the disabled send button to the reason it is disabled", () => {
    render(<Controlled disabled disabledReason="Add a model in Settings." />);
    const send = screen.getByRole("button", { name: "Send message" });
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription("Add a model in Settings.");
    expect(screen.queryByLabelText("Chat message")).toBeNull();
  });

  it("renders the header inside the box and the note under it", () => {
    const { container } = render(
      <Controlled header={<span>Current view</span>} note="Answers may contain mistakes." />,
    );
    const box = container.querySelector("[data-chat-composer]");
    expect(box).toHaveTextContent("Current view");
    expect(box).not.toHaveTextContent("Answers may contain mistakes.");
    expect(screen.getByLabelText("Chat message")).toHaveAccessibleDescription(
      "Answers may contain mistakes.",
    );
  });
});
