import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EmailPanel } from "./EmailPanel";
import { csrfRoute, envelope, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function setup(routes: Parameters<typeof mockApi>[0] = {}) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const handlers = { onClose: vi.fn(), onOutOfDate: vi.fn(), onAccessLost: vi.fn() };
  render(<EmailPanel revision={3} {...handlers} />);
  return { ...api, ...handlers };
}

const sent = (calls: Array<{ key: string; init?: RequestInit }>) =>
  calls.filter((c) => c.key === "POST /api/email").map((c) => JSON.parse(String(c.init?.body)));
const type = async (to: string, note = "") => {
  await userEvent.type(screen.getByLabelText("To"), to);
  if (note) await userEvent.type(screen.getByLabelText(/^Message/), note);
};
const send = () => userEvent.click(screen.getByRole("button", { name: "Send email" }));

describe("EmailPanel", () => {
  it("says what will be attached and puts the cursor in the address box", () => {
    setup();
    expect(screen.getByRole("heading", { name: "Send by email" })).toBeInTheDocument();
    expect(screen.getByText(/one-page canvas and the appendix as PDFs/)).toBeInTheDocument();
    expect(screen.getByLabelText("To")).toHaveFocus();
  });

  it("sends the confirmed revision, the parsed addresses and the note, then confirms to the user", async () => {
    const { calls } = setup({ "POST /api/email": () => json({ sent: 2 }) });
    await type("a@example.com, B@example.org a@example.com", "Please read before Friday.");
    await send();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Sent to a@example.com, B@example.org, with the one-page canvas and the appendix attached as PDFs.",
    );
    expect(sent(calls)).toEqual([
      { revision: 3, to: ["a@example.com", "B@example.org"], message: "Please read before Friday." },
    ]);
    expect(screen.queryByLabelText("To")).not.toBeInTheDocument(); // the form is done, nothing to resend by accident
  });

  it("closes from Done and from Cancel", async () => {
    const done = setup({ "POST /api/email": () => json({ sent: 1 }) });
    await type("a@example.com");
    await send();
    await userEvent.click(await screen.findByRole("button", { name: "Done" }));
    expect(done.onClose).toHaveBeenCalled();
  });

  it("can be cancelled without sending", async () => {
    const { onClose, calls } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onClose).toHaveBeenCalled();
    expect(sent(calls)).toHaveLength(0);
  });

  it.each([
    ["nothing", "", "Please enter at least one email address."],
    ["a word", "hello", "Please check the email addresses and try again."],
    ["a name and angle brackets", "Bob<b@example.com>", "Please check the email addresses and try again."],
    ["six addresses", "a@x.com b@x.com c@x.com d@x.com e@x.com f@x.com", "Please use no more than 5 addresses."],
  ])("asks for better addresses before sending anything when given %s", async (_name, text, message) => {
    const { calls } = setup();
    if (text) await type(text);
    await send();
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(sent(calls)).toHaveLength(0);
  });

  it("limits the note", () => {
    setup();
    expect(screen.getByLabelText(/^Message/)).toHaveAttribute("maxlength", "2000");
  });

  it.each([
    ["recipient_not_allowed", 422, "One of those addresses cannot be used. Please use a company address."],
    ["email_unavailable", 503, "Email is not switched on yet. You can still download the documents."],
    ["email_limit", 429, "You have sent the most emails allowed for this session."],
    ["send_in_progress", 409, "An email is already being sent. Please wait a moment."],
    ["email_busy", 503, "The email service is busy. Please try again shortly."],
    ["email_rejected", 502, "The email service did not accept it. Please check the addresses and try again."],
    ["email_too_large", 413, "The documents are too large to send by email. Please download them instead."],
    ["email_failed", 502, "The email could not be sent. Please try again."],
    ["appendix_failed", 500, "That could not be done. Please try again."],
    ["something_new", 500, "That could not be done. Please try again."],
  ])("shows fixed guidance for %s and lets the user try again", async (code, status, message) => {
    setup({ "POST /api/email": () => envelope(code, status) });
    await type("a@example.com", "SECRET-NOTE");
    await send();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    expect(screen.getByLabelText("To")).toHaveValue("a@example.com"); // what was typed is kept
    expect(screen.getByLabelText(/^Message/)).toHaveValue("SECRET-NOTE");
    expect(screen.getByRole("button", { name: "Send email" })).toBeEnabled();
  });

  it.each(["revision_conflict", "not_confirmed", "canvas_not_ready", "conversation_changed", "turn_in_progress"])(
    "hands %s to the review page, which re-reads the canvas",
    async (code) => {
      const { onOutOfDate, onAccessLost } = setup({ "POST /api/email": () => envelope(code, 409) });
      await type("a@example.com");
      await send();
      await waitFor(() => expect(onOutOfDate).toHaveBeenCalledTimes(1));
      expect(onOutOfDate.mock.calls[0][0]).toMatchObject({ code });
      expect(onAccessLost).not.toHaveBeenCalled();
    },
  );

  it("returns to entry when the session ended", async () => {
    const { onAccessLost } = setup({ "POST /api/email": () => envelope("unauthenticated", 401) });
    await type("a@example.com");
    await send();
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
  });

  it("only sends once while a send is running", async () => {
    let release!: () => void;
    const { calls } = setup({
      "POST /api/email": () => new Promise<Response>((resolve) => (release = () => resolve(json({ sent: 1 })))),
    });
    await type("a@example.com");
    await send();
    const busy = screen.getByRole("button", { name: "Sending…" });
    expect(busy).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("To"), "{Enter}"); // pressing Enter cannot send again
    expect(sent(calls)).toHaveLength(1);
    release();
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("treats a lost connection as a plain failure", async () => {
    mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/email": () => {
        throw new TypeError("network down");
      },
    });
    render(<EmailPanel revision={3} onClose={vi.fn()} onOutOfDate={vi.fn()} onAccessLost={vi.fn()} />);
    await type("a@example.com");
    await send();
    expect(await screen.findByRole("alert")).toHaveTextContent("That could not be done. Please try again.");
  });
});
