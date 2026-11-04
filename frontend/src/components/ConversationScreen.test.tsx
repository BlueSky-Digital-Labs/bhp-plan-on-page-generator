import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { controlledStream, csrfRoute, envelope, json, mockApi, ndjson } from "../test/mockApi";
import { ConversationScreen } from "./ConversationScreen";

const USER_ID = "1".repeat(32);
const BOT_ID = "2".repeat(32);
const STREAM = `GET /api/conversation/turns/${BOT_ID}/stream`;

const user = (text = "My idea") => ({ turn_id: USER_ID, role: "user", text, status: "complete" });
const bot = (status: string, text = "") => ({ turn_id: BOT_ID, role: "assistant", text, status });
const convo = (revision: number, ...turns: unknown[]) => () => json({ revision, turns });
const withCanvas = (canvas: unknown, revision: number, ...turns: unknown[]) => () =>
  json({ revision, turns, canvas });
const created = (revision = 1) => () =>
  json({ revision, user_turn: user(), assistant_turn: bot("pending") }, 201);

afterEach(() => vi.unstubAllGlobals());

function setup(routes: Parameters<typeof mockApi>[0]) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, ...routes });
  const onAccessLost = vi.fn();
  const view = render(<ConversationScreen onAccessLost={onAccessLost} />);
  return { ...api, onAccessLost, ...view };
}

const body = (calls: { key: string; init?: RequestInit }[], key = "POST /api/conversation/turns") =>
  JSON.parse(String(calls.filter((c) => c.key === key).at(-1)!.init?.body));
const posts = (calls: { key: string }[]) => calls.filter((c) => c.key === "POST /api/conversation/turns");

async function typeAndSend(text = "My idea") {
  const box = await screen.findByLabelText(/Describe your idea|Your answer/);
  await userEvent.clear(box);
  await userEvent.type(box, text);
  await userEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("opening screen and history", () => {
  it("invites a plain-language idea with a labelled form and no template", async () => {
    setup({ "GET /api/conversation": convo(0) });
    expect(screen.getByRole("status")).toHaveTextContent("Loading your conversation");
    expect(await screen.findByText(/in your own words/)).toBeInTheDocument();
    expect(screen.getByLabelText("Describe your idea")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Describe your idea"), "x");
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
  });

  it("shows stored turns in order, naming each speaker in words and not by colour alone", async () => {
    setup({
      "GET /api/conversation": convo(
        2,
        user("First"),
        bot("complete", "Reply one"),
        { ...user("Second"), turn_id: "3".repeat(32) },
        { ...bot("complete", "Reply two"), turn_id: "4".repeat(32) },
      ),
    });
    const list = await screen.findByRole("list", { name: "Conversation" });
    const items = within(list).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      "You saidFirst",
      "Assistant saidReply one",
      "You saidSecond",
      "Assistant saidReply two",
    ]);
    expect(screen.getByLabelText("Your answer")).toBeInTheDocument();
  });

  it("shows a safe, retryable message if history cannot be loaded", async () => {
    const { calls } = setup({
      "GET /api/conversation": [() => envelope("internal_error", 500, "abcdefabcdefabcdefabcdefabcdefab"), convo(0)],
    });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not load your conversation");
    expect(alert).toHaveTextContent("abcdefabcdefabcdefabcdefabcdefab");
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    await userEvent.click(screen.getByRole("button", { name: "Reload conversation" }));
    expect(await screen.findByLabelText("Describe your idea")).toBeInTheDocument();
    expect(calls.filter((c) => c.key === "GET /api/conversation")).toHaveLength(2);
  });

  it("ends the view and shows nothing when the session has expired (401)", async () => {
    const { onAccessLost } = setup({ "GET /api/conversation": () => envelope("unauthenticated", 401) });
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
    expect(screen.queryByLabelText(/Describe your idea|Your answer/)).not.toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });
});

describe("chat window", () => {
  const messages = () => document.querySelector(".chat__messages") as HTMLElement;
  const fakeLayout = (scrollHeight: number) =>
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("chat__messages") ? scrollHeight : 0;
    });

  it("puts the user's and the assistant's messages on opposite sides, named in words", async () => {
    setup({ "GET /api/conversation": convo(2, user("First"), bot("complete", "Reply one")) });
    await screen.findByText("First");
    const items = within(screen.getByRole("list", { name: "Conversation" })).getAllByRole("listitem");
    expect(items[0]).toHaveClass("turn--user");
    expect(items[1]).toHaveClass("turn--assistant");
    expect(screen.getByRole("region", { name: "Chat with the assistant" })).toHaveTextContent("AI assistant");
    // Avatars are decorative: the speaker is always named in text.
    expect(document.querySelectorAll(".avatar[aria-hidden=true]")).toHaveLength(3); // header + 2 turns
  });

  it("opens at the newest message", async () => {
    const layout = fakeLayout(1000);
    setup({ "GET /api/conversation": convo(2, user("First"), bot("complete", "Reply one")) });
    await screen.findByText("First");
    expect(messages().scrollTop).toBe(1000);
    layout.mockRestore();
  });

  it("follows the reply as it streams, but leaves the user alone once they scroll up to re-read", async () => {
    let height = 1000;
    const layout = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) {
      return this.classList.contains("chat__messages") ? height : 0;
    });
    const stream = controlledStream();
    setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending")), convo(1, user(), bot("complete", "Done"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    await typeAndSend("My idea");
    stream.push({ type: "delta", text: "First " });
    await screen.findByTestId("live-reply");
    height = 1200;
    stream.push({ type: "delta", text: "more " });
    await waitFor(() => expect(messages().scrollTop).toBe(1200)); // following the newest text

    messages().scrollTop = 100; // the user scrolls up to re-read
    fireEvent.scroll(messages());
    height = 1500;
    stream.push({ type: "delta", text: "and more" });
    await waitFor(() => expect(screen.getByTestId("live-reply")).toHaveTextContent("First more and more"));
    expect(messages().scrollTop).toBe(100); // not yanked back down
    stream.push({ type: "complete" });
    stream.end();
    layout.mockRestore();
  });

  it("shows a typing indicator, not an empty bubble, before the first words arrive", async () => {
    const stream = controlledStream();
    setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    await typeAndSend("My idea");
    await screen.findByTestId("live-reply");
    expect(document.querySelector(".typing")).not.toBeNull();
    stream.push({ type: "delta", text: "Hi" });
    await waitFor(() => expect(document.querySelector(".typing")).toBeNull());
  });
});

describe("interview phase", () => {
  const withPhase = (phase: string) => () =>
    json({ revision: 1, turns: [user(), { ...bot("complete", "Which team owns this?") }], progression: { phase } });

  it.each([
    ["clarification", "Clarifying a detail"],
    ["data_and_systems", "Checking data and systems"],
    ["review_ready", "Ready to review"],
  ])("shows when the server says the interview is in %s", async (phase, label) => {
    setup({ "GET /api/conversation": withPhase(phase) });
    expect(await screen.findByTestId("interview-phase")).toHaveTextContent(label);
  });

  it("shows nothing extra in the ordinary interview phase or when the server sends no phase", async () => {
    setup({ "GET /api/conversation": withPhase("interview") });
    await screen.findByLabelText(/Describe your idea|Your answer/);
    expect(screen.queryByTestId("interview-phase")).not.toBeInTheDocument();
  });

  it("never renders anything the response says beyond the fixed label for a known phase", async () => {
    setup({ "GET /api/conversation": withPhase("<img src=x onerror=alert(1)>") });
    await screen.findByLabelText(/Describe your idea|Your answer/);
    expect(screen.queryByTestId("interview-phase")).not.toBeInTheDocument();
    expect(document.querySelector("img[src='x']")).toBeNull();
  });
});

describe("canvas draft", () => {
  it("shows what the server has saved beside the conversation, and refreshes it after each reply", async () => {
    const canvas0 = { fields: {}, missing_required: Array(14).fill("x"), ready_for_review: false };
    const canvas1 = {
      fields: { achieve: "Faster handover" },
      missing_required: Array(13).fill("x"),
      ready_for_review: false,
    };
    setup({
      "GET /api/conversation": [
        withCanvas(canvas0, 0),
        withCanvas(canvas0, 1, user(), bot("pending")),
        withCanvas(canvas1, 1, user(), bot("complete", "Thanks. What would you like to call this solution?")),
      ],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => ndjson([{ type: "delta", text: "Thanks. " }, { type: "complete" }]),
    });
    expect(await screen.findByTestId("canvas-progress")).toHaveTextContent("14 required answers still to go");
    await typeAndSend("Faster handover");
    await waitFor(() => expect(screen.getByTestId("canvas-progress")).toHaveTextContent("13 required answers still to go"));
    expect(within(screen.getByTestId("canvas-achieve")).getByText("Faster handover")).toBeInTheDocument();
  });

  it("announces readiness from the server's verdict, not from anything the page works out itself", async () => {
    const ready = { fields: { team: "Ops" }, missing_required: [], ready_for_review: true };
    setup({ "GET /api/conversation": withCanvas(ready, 2, user(), bot("complete", "All done")) });
    expect(await screen.findByTestId("canvas-progress")).toHaveTextContent("Ready for review");
  });
});

describe("submitting and streaming", () => {
  it("sends the id, expected revision and text, then shows progress separately from stored history", async () => {
    const stream = controlledStream();
    const { calls } = setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending")), convo(1, user(), bot("complete", "Hello there."))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    await typeAndSend("My idea");

    const sent = body(calls);
    expect(sent).toEqual({ submission_id: expect.stringMatching(/^[\w-]{8,64}$/), expected_revision: 0, text: "My idea" });
    const headers = calls.find((c) => c.key === "POST /api/conversation/turns")!.init?.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("test-csrf-token");

    // Streaming: transient text is visibly pending and is NOT in the committed history yet.
    stream.push({ type: "delta", text: "Hello " });
    expect(await screen.findByTestId("live-reply")).toHaveTextContent("Hello");
    expect(screen.getByText(/reply in progress \(not yet confirmed\)/)).toBeInTheDocument();
    expect(screen.getByTestId("reply-status")).toHaveTextContent("The assistant is replying");
    expect(screen.getByTestId("persistence-status")).toHaveTextContent("Your answers are saved");
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();

    stream.push({ type: "delta", text: "there." });
    await waitFor(() => expect(screen.getByTestId("live-reply")).toHaveTextContent("Hello there."));
    stream.push({ type: "complete" });
    stream.end();

    // Completion: the stored turn replaces the transient text, shown once.
    await waitFor(() => expect(screen.queryByTestId("live-reply")).not.toBeInTheDocument());
    expect(screen.getAllByText("Hello there.")).toHaveLength(1);
    expect(screen.getByText("Assistant said")).toBeInTheDocument();
    expect(screen.getByTestId("reply-status")).toBeEmptyDOMElement();
  });

  it("ignores a second click while the answer is being sent", async () => {
    let release!: (r: Response) => void;
    const pending = new Promise<Response>((resolve) => (release = resolve));
    const { calls } = setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("complete", "ok"))],
      "POST /api/conversation/turns": () => pending,
    });
    await typeAndSend();
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    expect(screen.getByTestId("persistence-status")).toHaveTextContent("Saving your answer");
    await userEvent.click(screen.getByRole("button", { name: "Sending…" }));
    release(json({ revision: 1, user_turn: user(), assistant_turn: bot("complete", "ok") }, 201));
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled()); // empty draft
    expect(posts(calls)).toHaveLength(1);
  });

  it("keeps the same submission id when retrying, and a new one only for different text", async () => {
    const { calls } = setup({
      "GET /api/conversation": [convo(0), convo(0), convo(1, user(), bot("complete", "ok"))],
      "POST /api/conversation/turns": [
        () => envelope("service_unavailable", 503, "abcdefabcdefabcdefabcdefabcdefab"),
        () => envelope("service_unavailable", 503),
        () => json({ revision: 1, user_turn: user(), assistant_turn: bot("complete", "ok") }, 201),
      ],
    });
    await typeAndSend("My idea");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("could not confirm that your answer was saved");
    expect(alert).toHaveTextContent("it will not be added twice");
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    const first = body(calls).submission_id;

    await userEvent.click(screen.getByRole("button", { name: "Try sending again" }));
    await screen.findByRole("alert");
    expect(body(calls).submission_id).toBe(first); // retry: same id, so it can never be recorded twice

    await userEvent.type(screen.getByLabelText("Describe your idea"), " plus an edit");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(3));
    expect(body(calls).submission_id).not.toBe(first); // edited text is a different answer
    expect(body(calls).text).toBe("My idea plus an edit");
  });

  it("explains a revision conflict and offers a keyboard-reachable reload instead of a blind retry", async () => {
    const { calls } = setup({
      "GET /api/conversation": [convo(0), convo(1, user("Someone else's tab"), bot("complete", "ok"))],
      "POST /api/conversation/turns": () => envelope("revision_conflict", 409),
    });
    await typeAndSend("Mine");
    expect(await screen.findByRole("alert")).toHaveTextContent("Your conversation has changed");
    expect(screen.queryByRole("button", { name: "Try sending again" })).not.toBeInTheDocument();

    await userEvent.tab(); // keyboard only from here
    const reload = screen.getByRole("button", { name: "Reload conversation" });
    reload.focus();
    expect(reload).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Someone else's tab")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(calls.filter((c) => c.key === "GET /api/conversation")).toHaveLength(2);
  });
});

describe("interruptions and refresh", () => {
  it("after a dropped stream it re-reads stored state first, never claims the answer was lost, and recovers", async () => {
    const { calls } = setup({
      "GET /api/conversation": [
        convo(0),
        convo(1, user(), bot("pending")), // after submit
        convo(1, user(), bot("pending")), // re-read after the stream broke
        convo(1, user(), bot("complete", "Recovered reply")), // after the retry completes
      ],
      "POST /api/conversation/turns": created(),
      [STREAM]: [
        () => ndjson([{ type: "delta", text: "Half a " }], { breakAfter: true }),
        () => ndjson([{ type: "delta", text: "Recovered reply" }, { type: "complete" }]),
      ],
    });
    await typeAndSend();

    expect(await screen.findByTestId("reply-status")).toHaveTextContent("interrupted. Your answer is saved");
    expect(screen.queryByText(/Half a/)).not.toBeInTheDocument(); // transient text is discarded
    expect(screen.getAllByText("My idea")).toHaveLength(1);
    const keys = calls.map((c) => c.key);
    expect(keys.lastIndexOf("GET /api/conversation")).toBeGreaterThan(keys.indexOf(STREAM)); // re-read

    await userEvent.click(screen.getByRole("button", { name: "Get the reply" }));
    expect(await screen.findByText("Recovered reply")).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(1); // the answer was never resubmitted
    expect(screen.getAllByText("My idea")).toHaveLength(1);
  });

  it("if the reply was stored before the connection dropped, it shows the stored reply and no error", async () => {
    setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending")), convo(1, user(), bot("complete", "Stored"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => ndjson([{ type: "delta", text: "Sto" }], { breakAfter: true }),
    });
    await typeAndSend();
    expect(await screen.findByText("Stored")).toBeInTheDocument();
    expect(screen.getByTestId("reply-status")).toBeEmptyDOMElement();
    expect(screen.queryByRole("button", { name: "Get the reply" })).not.toBeInTheDocument();
  });

  it("a failed reply offers a retry and keeps the answer recorded", async () => {
    const { calls } = setup({
      "GET /api/conversation": [
        convo(0),
        convo(1, user(), bot("pending")),
        convo(1, user(), bot("failed")),
        convo(1, user(), bot("complete", "Now it works")),
      ],
      "POST /api/conversation/turns": created(),
      [STREAM]: [
        () => ndjson([{ type: "failed", reason: "unavailable" }]),
        () => ndjson([{ type: "delta", text: "Now it works" }, { type: "complete" }]),
      ],
    });
    await typeAndSend();
    expect(await screen.findByTestId("reply-status")).toHaveTextContent("could not reply just now. Your answer is saved");
    await userEvent.click(screen.getByRole("button", { name: "Try the reply again" }));
    expect(await screen.findByText("Now it works")).toBeInTheDocument();
    expect(posts(calls)).toHaveLength(1);
  });

  it("on refresh shows stored turns and a recoverable pending state, without duplicating the answer", async () => {
    setup({ "GET /api/conversation": convo(1, user("Already sent"), bot("pending")) });
    expect(await screen.findByText("Already sent")).toBeInTheDocument();
    expect(screen.getAllByText("Already sent")).toHaveLength(1);
    expect(screen.getByTestId("reply-status")).toHaveTextContent("A reply is waiting");
    expect(screen.getByRole("button", { name: "Get the reply" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("stops the stream when the user leaves the screen (browser back)", async () => {
    const stream = controlledStream();
    let streamSignal: AbortSignal | undefined;
    const api = mockApi({
      "GET /api/csrf": csrfRoute,
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    const original = api.fn.getMockImplementation()!;
    api.fn.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/stream")) streamSignal = init?.signal as AbortSignal;
      return original(input, init);
    });
    const { unmount } = render(<ConversationScreen onAccessLost={vi.fn()} />);
    await typeAndSend();
    stream.push({ type: "delta", text: "x" });
    await screen.findByTestId("live-reply");
    unmount();
    expect(streamSignal?.aborted).toBe(true);
  });
});

describe("content safety", () => {
  it("renders user and model markup as inert text", async () => {
    const hostile = '<img src=x onerror="alert(1)"> <script>alert(2)</script> & "q"';
    const { container } = setup({
      "GET /api/conversation": convo(1, user(hostile), bot("complete", "<b>bold</b> <a href='javascript:alert(3)'>x</a>")),
    });
    expect(await screen.findByText(hostile)).toBeInTheDocument();
    expect(screen.getByText("<b>bold</b> <a href='javascript:alert(3)'>x</a>")).toBeInTheDocument();
    expect(container.querySelector("img, script, b, a")).toBeNull();
  });

  it("renders streamed model markup as inert text too", async () => {
    const stream = controlledStream();
    const { container } = setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    await typeAndSend();
    stream.push({ type: "delta", text: "<img src=x onerror=alert(1)>" });
    expect(await screen.findByTestId("live-reply")).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });

  it("never writes idea content to browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const idb = vi.fn();
    vi.stubGlobal("indexedDB", { open: idb }); // jsdom has none: prove the app never opens one
    setup({
      "GET /api/conversation": [convo(0), convo(1, user("Secret plan"), bot("complete", "Reply")), convo(1, user("Secret plan"), bot("complete", "Reply"))],
      "POST /api/conversation/turns": () => json({ revision: 1, user_turn: user("Secret plan"), assistant_turn: bot("complete", "Reply") }, 201),
    });
    await typeAndSend("Secret plan");
    await screen.findByText("Reply");
    expect(setItem).not.toHaveBeenCalled();
    expect(idb).not.toHaveBeenCalled();
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain("Secret plan");
  });
});
