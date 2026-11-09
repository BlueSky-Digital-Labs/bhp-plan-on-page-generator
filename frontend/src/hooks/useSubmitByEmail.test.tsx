import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSubmitByEmail } from "./useSubmitByEmail";
import { csrfRoute, envelope, json, mockApi } from "../test/mockApi";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function Harness({ revision, ...handlers }: { revision: number; onOutOfDate: () => void; onAccessLost: () => void }) {
  const email = useSubmitByEmail({ revision, ...handlers });
  return (
    <div>
      <button type="button" onClick={() => void email.submit()} disabled={email.sending}>
        {email.sending ? "Submitting…" : "Submit"}
      </button>
      {email.sent && <p role="status">sent</p>}
      {email.error && <p role="alert">{email.error}</p>}
    </div>
  );
}

function setup(routes: Parameters<typeof mockApi>[0] = {}, revision = 3) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const handlers = { onOutOfDate: vi.fn(), onAccessLost: vi.fn() };
  const view = render(<Harness revision={revision} {...handlers} />);
  return { ...api, ...handlers, view };
}

const posts = (calls: Array<{ key: string; init?: RequestInit }>) => calls.filter((c) => c.key === "POST /api/email");
const click = () => userEvent.click(screen.getByRole("button", { name: /Submit/ }));

describe("useSubmitByEmail", () => {
  it("sends the confirmed revision on one click and says so", async () => {
    const { calls } = setup({ "POST /api/email": () => json({ sent: 1 }) });
    await click();
    expect(await screen.findByRole("status")).toHaveTextContent("sent");
    expect(posts(calls)).toHaveLength(1);
    expect(JSON.parse(String(posts(calls)[0].init?.body))).toEqual({ revision: 3 });
  });

  it("does not send twice while a send is running", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => (release = resolve));
    const { calls } = setup({
      "POST /api/email": async () => {
        await gate;
        return json({ sent: 1 });
      },
    });
    await click();
    await userEvent.click(screen.getByRole("button", { name: "Submitting…" })); // disabled: ignored
    release();
    await screen.findByRole("status");
    expect(posts(calls)).toHaveLength(1);
  });

  it.each([
    ["email_unavailable", 503, /not switched on yet/],
    ["email_limit", 429, /all the email submissions allowed/],
    ["send_in_progress", 409, /already being sent/],
    ["email_busy", 503, /busy/],
    ["email_rejected", 502, /did not accept it/],
    ["email_too_large", 413, /too large/],
    ["email_failed", 502, /could not be sent/],
    ["something_new", 500, /That could not be done/],
  ])("shows fixed guidance for %s and never the server's words", async (code, status, text) => {
    setup({ "POST /api/email": () => envelope(code, status) });
    await click();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(text);
    expect(alert).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
  });

  it("hands the canvas-changed errors to the page, which re-reads itself", async () => {
    const { onOutOfDate } = setup({ "POST /api/email": () => envelope("revision_conflict", 409) });
    await click();
    await waitFor(() => expect(onOutOfDate).toHaveBeenCalledOnce());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hands a lost session to the page", async () => {
    const { onAccessLost } = setup({ "POST /api/email": () => envelope("unauthenticated", 401) });
    await click();
    await waitFor(() => expect(onAccessLost).toHaveBeenCalledOnce());
  });

  it("clears the last result when the revision changes: it must be confirmed and sent again", async () => {
    const { view, onOutOfDate, onAccessLost } = setup({ "POST /api/email": () => json({ sent: 1 }) });
    await click();
    await screen.findByRole("status");
    view.rerender(<Harness revision={4} onOutOfDate={onOutOfDate} onAccessLost={onAccessLost} />);
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeInTheDocument());
  });
});
