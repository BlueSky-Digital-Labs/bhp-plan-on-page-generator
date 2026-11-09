import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type Handler, controlledStream, csrfRoute, envelope, json, mockApi, ndjson } from "../test/mockApi";
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

// The "save your progress" card reads the download status once an answer is in; most tests are not about it.
const idleDownloads = () =>
  json({
    revision: 1,
    canvas_downloaded: false,
    appendix_downloaded: false,
    purge_eligible: false,
    lock_reason: "both_not_downloaded",
    complete: false,
  });

function setup(routes: Parameters<typeof mockApi>[0]) {
  const api = mockApi({ "GET /api/csrf": csrfRoute, "GET /api/downloads": idleDownloads, ...routes });
  const onAccessLost = vi.fn();
  const view = render(<ConversationScreen onAccessLost={onAccessLost} />);
  return { ...api, onAccessLost, ...view };
}

const body = (calls: { key: string; init?: RequestInit }[], key = "POST /api/conversation/turns") =>
  JSON.parse(String(calls.filter((c) => c.key === key).at(-1)!.init?.body));
const posts = <T extends { key: string }>(calls: T[]) => calls.filter((c) => c.key === "POST /api/conversation/turns");

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
    ["changing", "Changing an answer"],
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
  it("shows only the chat at the start, then the canvas checklist beside it once the conversation begins, refreshed after each reply", async () => {
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
    // At the start: the chat box and nothing else.
    expect(await screen.findByLabelText(/Describe your idea/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your canvas so far" })).not.toBeInTheDocument();
    expect(screen.queryByTestId("canvas-progress")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Save your progress" })).not.toBeInTheDocument();

    await typeAndSend("Faster handover");
    expect(await screen.findByRole("heading", { name: "Your canvas so far" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("canvas-progress")).toHaveTextContent("13 required answers still to go"));
    expect(within(screen.getByTestId("canvas-achieve")).getByText("Answered")).toBeInTheDocument(); // a tick, not the answer
    expect(screen.queryByText("Faster handover", { selector: ".canvas-panel *" })).not.toBeInTheDocument();
  });

  it("keeps the canvas fields in a small box that scrolls, which a keyboard user can reach and scroll", async () => {
    const canvas = { fields: { team: "Ops" }, missing_required: ["priority"], ready_for_review: false };
    setup({ "GET /api/conversation": withCanvas(canvas, 1, user(), bot("complete", "Which team?")) });
    const box = await screen.findByRole("region", { name: "Canvas fields so far" });
    expect(box).toHaveAttribute("tabindex", "0");
    expect(within(box).getByTestId("canvas-team")).toBeInTheDocument();
    box.focus();
    expect(box).toHaveFocus();
  });

  it("puts the canvas checklist and then the save card in a side column next to the chat", async () => {
    const canvas = { fields: {}, missing_required: ["team"], ready_for_review: false };
    setup({ "GET /api/conversation": withCanvas(canvas, 1, user(), bot("complete", "Which team?")) });
    const chat = await screen.findByRole("region", { name: "Chat with the assistant" });
    const canvasBox = await screen.findByRole("heading", { name: "Your canvas so far" });
    const save = await screen.findByRole("heading", { name: "Save your progress" });
    const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    const side = screen.getByRole("complementary", { name: "Your progress" });
    expect(side).toContainElement(canvasBox);
    expect(side).toContainElement(save);
    expect(side).not.toContainElement(chat);
    expect(follows(canvasBox, save)).toBe(true);
  });

  it("keeps the save card's place while a reply is coming, without offering the downloads", async () => {
    const canvas = { fields: {}, missing_required: ["team"], ready_for_review: false };
    setup({
      "GET /api/conversation": withCanvas(canvas, 1, user(), bot("pending")),
      "GET /api/conversation/turns/22222222222222222222222222222222/stream": () => new Promise<Response>(() => {}),
    });
    expect(await screen.findByText(/once the assistant has replied/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Save your progress" })).not.toBeInTheDocument();
  });

  it("announces readiness from the server's verdict, not from anything the page works out itself", async () => {
    const ready = { fields: { team: "Ops" }, missing_required: [], ready_for_review: true };
    setup({ "GET /api/conversation": withCanvas(ready, 2, user(), bot("complete", "All done")) });
    expect(await screen.findByTestId("canvas-progress")).toHaveTextContent("Ready for review");
  });

  it("offers 'Generate canvas' in the chat under the assistant's closing message, with the answer box still there", async () => {
    const ready = { fields: { team: "Ops" }, missing_required: [], ready_for_review: true };
    setup({ "GET /api/conversation": withCanvas(ready, 2, user(), bot("complete", "That's everything I need.")) });
    const chat = await screen.findByRole("region", { name: "Chat with the assistant" });
    const button = await within(chat).findByRole("button", { name: "Generate canvas" });
    expect(within(chat).getByLabelText("Your answer")).toBeInTheDocument();
    expect(within(chat).getByRole("button", { name: "Send" })).toBeInTheDocument();
    const message = within(chat).getByText("That's everything I need.");
    expect(Boolean(message.compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
  });

  it("cannot be reopened once the canvas is confirmed: it goes to the review page", async () => {
    const ready = { fields: { team: "Ops" }, missing_required: [], ready_for_review: true };
    setup({
      "GET /api/conversation": () =>
        json({ revision: 2, turns: [user(), bot("complete", "Done")], canvas: ready, confirmed_revision: 2 }),
    });
    await waitFor(() => expect(window.location.pathname).toBe("/review"));
  });

  it("does not offer it while the canvas is unfinished", async () => {
    const canvas = { fields: {}, missing_required: ["team"], ready_for_review: false };
    setup({ "GET /api/conversation": withCanvas(canvas, 1, user(), bot("complete", "Which team?")) });
    await screen.findByRole("heading", { name: "Your canvas so far" });
    expect(screen.queryByRole("button", { name: "Generate canvas" })).not.toBeInTheDocument();
  });
});

describe("submitting and streaming", () => {
  it("sends on Enter only when there is text; Shift+Enter makes a new line", async () => {
    const { calls } = setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("complete", "Hello."))],
      "POST /api/conversation/turns": created(),
    });
    const box = await screen.findByLabelText(/Describe your idea/);
    const posts = () => calls.filter((c) => c.key === "POST /api/conversation/turns").length;

    await userEvent.type(box, "{Enter}"); // nothing typed: no send, no stray new line
    await userEvent.type(box, "   {Enter}"); // only spaces: no send
    expect(posts()).toBe(0);

    await userEvent.clear(box);
    await userEvent.type(box, "My idea{Shift>}{Enter}{/Shift}more");
    expect(box).toHaveValue("My idea\nmore");
    expect(posts()).toBe(0);

    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(posts()).toBe(1));
    expect(calls.find((c) => c.key === "POST /api/conversation/turns")?.init?.body).toContain("My idea\\nmore");
  });

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

describe("attaching a file", () => {
  const BLOCK = "[[Attached file: idea.json | json]]\n{\n  \"idea\": \"Shift notes\"\n}\n[[End of attached file]]";
  const ATTACHED = { filename: "idea.json", kind: "json", characters: 1234, truncated: false, block: BLOCK };
  const UPLOAD = "POST /api/attachments?name=idea.json";
  const file = (name = "idea.json", size = 10) =>
    new File([new Uint8Array(size)], name, { type: "application/octet-stream" });
  const choose = (f: File) => userEvent.upload(screen.getByTestId("file-input"), f, { applyAccept: false });
  const attachOk = (extra: Record<string, Handler | Handler[]> = {}) => ({
    "GET /api/conversation": [convo(0), convo(1, user(), bot("complete", "Thanks."))],
    "POST /api/conversation/turns": created(),
    [STREAM]: () => ndjson([{ type: "complete" }]),
    [UPLOAD]: () => json(ATTACHED),
    ...extra,
  });

  it("offers a paperclip, reads the file and shows it as a chip the user can remove", async () => {
    setup(attachOk());
    const clip = await screen.findByRole("button", { name: /Attach a file/ });
    expect(clip).toBeEnabled();
    await choose(file());
    expect(await screen.findByText("idea.json")).toBeInTheDocument();
    expect(screen.getByText(/1,234 characters/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove idea.json" }));
    expect(screen.queryByText("idea.json")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled(); // nothing left to send
  });

  it("uploads the raw file with the forgery token and nothing else", async () => {
    const encoded = "POST /api/attachments?name=my%20idea.json";
    const { calls } = setup(attachOk({ [encoded]: () => json(ATTACHED) }));
    await screen.findByRole("button", { name: /Attach a file/ });
    const picked = file("my idea.json");
    await choose(picked);
    await waitFor(() => expect(calls.some((c) => c.key === encoded)).toBe(true));
    const sent = calls.find((c) => c.key === encoded)!.init!;
    expect(sent.body).toBe(picked);
    expect((sent.headers as Record<string, string>)["X-CSRF-Token"]).toBe("test-csrf-token");
  });

  it("can be sent with no typed text: the file's text is the answer", async () => {
    const { calls } = setup(attachOk());
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file());
    await screen.findByText("idea.json");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(body(calls).text).toBe(BLOCK);
    await waitFor(() => expect(screen.queryByText("idea.json")).not.toBeInTheDocument()); // cleared after sending
  });

  it("sends what was typed first, then the file's text, as one answer", async () => {
    const { calls } = setup(attachOk());
    const box = await screen.findByLabelText(/Describe your idea|Your answer/);
    await userEvent.type(box, "Here is our idea.");
    await choose(file());
    await screen.findByText("idea.json");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(body(calls).text).toBe(`Here is our idea.\n\n${BLOCK}`);
  });

  it("keeps the same submission id when retrying the same answer and file", async () => {
    const { calls } = setup(
      attachOk({
        "GET /api/conversation": convo(0),
        "POST /api/conversation/turns": [() => envelope("internal_error", 500), created()],
      }),
    );
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file());
    await screen.findByText("idea.json");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(screen.getByText("idea.json")).toBeInTheDocument(); // still attached after a failed send
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    const ids = posts(calls).map((c) => JSON.parse(String(c.init?.body)).submission_id);
    expect(ids[0]).toBe(ids[1]);
  });

  it("says so, without uploading, for a type that is not supported or a file that is too large", async () => {
    const { calls } = setup(attachOk());
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file("notes.txt"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Only PDF, Word (.docx), PowerPoint (.pptx) and JSON");
    await choose(file("big.pdf", 6 * 1024 * 1024));
    expect(await screen.findByRole("alert")).toHaveTextContent("That file is too large. The limit is 5 MB.");
    expect(calls.some((c) => c.key.startsWith("POST /api/attachments"))).toBe(false);
  });

  it.each([
    ["file_unreadable", 422, "could not be read"],
    ["file_has_no_text", 422, "No text was found"],
    ["file_too_large", 413, "too large"],
    ["internal_error", 500, "could not be attached"],
  ])("explains a %s refusal in fixed words and never shows what the server said", async (code, status, words) => {
    setup(attachOk({ [UPLOAD]: () => envelope(code, status) }));
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file());
    expect(await screen.findByRole("alert")).toHaveTextContent(words);
    expect(screen.queryByText(/SERVER-TEXT-MUST-NEVER-RENDER/)).not.toBeInTheDocument();
    expect(screen.queryByText("idea.json")).not.toBeInTheDocument();
  });

  it("returns to entry when the session ended while reading the file", async () => {
    const { onAccessLost } = setup(attachOk({ [UPLOAD]: () => envelope("unauthenticated", 401) }));
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file());
    await waitFor(() => expect(onAccessLost).toHaveBeenCalled());
  });

  it("warns when only the start of a long file will be sent", async () => {
    setup(attachOk({ [UPLOAD]: () => json({ ...ATTACHED, truncated: true }) }));
    await screen.findByRole("button", { name: /Attach a file/ });
    await choose(file());
    expect(await screen.findByText(/shortened to fit/)).toBeInTheDocument();
    expect(screen.getByText(/only that part will be sent/)).toBeInTheDocument();
  });

  it("shows an attached file in the conversation folded away, with the typed words visible", async () => {
    const text = `Here is our idea.\n\n${BLOCK}`;
    setup({ "GET /api/conversation": convo(1, { ...user(text) }, bot("complete", "Thanks.")) });
    expect(await screen.findByText("Here is our idea.")).toBeInTheDocument();
    const summary = screen.getByText(/Attached file: idea.json \(/);
    expect(summary.closest("details")).not.toHaveAttribute("open");
    expect(summary).toHaveTextContent("characters");
    expect(document.querySelector(".turn__file-text")).toHaveTextContent('"idea": "Shift notes"');
  });

  it("shows a file's text as inert text, never as markup", async () => {
    const hostile = "[[Attached file: x.json | json]]\n<img src=x onerror=alert(1)>\n[[End of attached file]]";
    setup({ "GET /api/conversation": convo(1, { ...user(hostile) }, bot("complete", "Thanks.")) });
    await screen.findByText(/Attached file: x.json/);
    expect(document.querySelector("img[src='x']")).toBeNull();
    expect(document.querySelector(".turn__file-text")).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("never treats the assistant's text as an attachment", async () => {
    setup({ "GET /api/conversation": convo(1, user(), bot("complete", BLOCK)) });
    await screen.findByText(/Attached file: idea.json/, { selector: "p" });
    const chat = screen.getByRole("region", { name: "Chat with the assistant" });
    expect(chat.querySelector("details")).toBeNull(); // nothing in the chat is folded away like an attachment
  });

  it("cannot attach while an answer is being sent or a reply is on its way", async () => {
    const stream = controlledStream();
    setup({
      "GET /api/conversation": [convo(0), convo(1, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => stream.response(),
    });
    await typeAndSend("My idea");
    await waitFor(() => expect(screen.getByRole("button", { name: /Attach a file/ })).toBeDisabled());
    stream.end();
  });
});

describe("saving progress to stop and carry on later", () => {
  const incomplete = { fields: { team: "Ops" }, missing_required: ["priority"], ready_for_review: false };
  const complete = { fields: { team: "Ops" }, missing_required: [], ready_for_review: true };
  const card = () => screen.queryByRole("heading", { level: 2, name: "Save your progress" });

  it("offers nothing before the first answer", async () => {
    setup({ "GET /api/conversation": convo(0) });
    await screen.findByLabelText(/Describe your idea/);
    expect(card()).not.toBeInTheDocument();
  });

  it("offers it once an answer is in and the canvas is still unfinished, without calling the server to stop", async () => {
    const { calls } = setup({
      "GET /api/conversation": withCanvas(incomplete, 1, user(), bot("complete", "Which team?")),
    });
    expect(await screen.findByRole("heading", { level: 2, name: "Save your progress" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download draft canvas/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Stop session/ })).toBeDisabled(); // the gate is still locked
    expect(calls.some((c) => c.key === "POST /api/session/purge")).toBe(false);
  });

  it("is not shown for a finished canvas, which has its own review page", async () => {
    setup({ "GET /api/conversation": withCanvas(complete, 1, user(), bot("complete", "All done")) });
    await screen.findByRole("button", { name: "Generate canvas" });
    expect(card()).not.toBeInTheDocument();
  });

  it("is hidden while a reply is still coming, so nothing half-written can be saved", async () => {
    setup({
      "GET /api/conversation": withCanvas(incomplete, 1, user(), bot("pending")),
      "GET /api/conversation/turns/22222222222222222222222222222222/stream": () =>
        new Promise<Response>(() => {}), // never answers
    });
    await screen.findByText("My idea");
    expect(card()).not.toBeInTheDocument();
  });
});

describe("answering from a list of choices", () => {
  const PRIORITY = { multiple: false, options: ["Must-have", "Nice-to-have", "Exploratory"] };
  const TYPES = { multiple: true, options: ["Find Information", "Generate Reports", "Predict Issues"] };
  // The same history, with what the server says is outstanding.
  const withQuestion = (choices: unknown, text = "What priority would you give this idea?", revision = 1, kind = "missing") => () =>
    json({
      revision,
      turns: [user(), bot("complete", text)],
      canvas: { fields: {}, missing_required: ["priority"], ready_for_review: false },
      progression: { phase: "interview", outstanding_question: { target: "priority", kind, text, choices } },
    });
  const box = () => screen.queryByLabelText(/Describe your idea|Your answer/);

  it("shows the options to pick from instead of a text box, and sends the picked wording as the answer", async () => {
    const { calls } = setup({
      "GET /api/conversation": [withQuestion(PRIORITY), convo(2, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => controlledStream().response(),
    });
    expect(await screen.findByRole("group", { name: "Choose one" })).toBeInTheDocument();
    expect(box()).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Must-have"));
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(body(calls)).toEqual({ submission_id: expect.stringMatching(/^[\w-]{8,64}$/), expected_revision: 1, text: "Must-have" });
  });

  it("lets several be picked for a question that takes more than one", async () => {
    const { calls } = setup({
      "GET /api/conversation": [withQuestion(TYPES, "What kind of help do you want?"), convo(2, user(), bot("pending"))],
      "POST /api/conversation/turns": created(),
      [STREAM]: () => controlledStream().response(),
    });
    await userEvent.click(await screen.findByLabelText("Predict Issues"));
    await userEvent.click(screen.getByLabelText("Find Information"));
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(1));
    expect(body(calls).text).toBe("Find Information, Predict Issues");
  });

  it("goes back to the text box when the user chooses to type their own answer", async () => {
    setup({ "GET /api/conversation": withQuestion(PRIORITY) });
    await userEvent.click(await screen.findByRole("button", { name: "Type my own answer instead" }));
    expect(await screen.findByLabelText(/Your answer/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Choose one" })).not.toBeInTheDocument();
  });

  it("uses the text box when the server offers no list for the question", async () => {
    setup({ "GET /api/conversation": withQuestion(null, "Why did you give this idea that priority?", 1, "probe") });
    expect(await screen.findByLabelText(/Your answer/)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Choose one" })).not.toBeInTheDocument();
  });

  it("keeps showing the choices, with the pick still there, if sending fails, and retries the same answer", async () => {
    const { calls } = setup({
      "GET /api/conversation": withQuestion(PRIORITY),
      "POST /api/conversation/turns": [() => envelope("internal_error", 500), created()],
      [STREAM]: () => controlledStream().response(),
    });
    await userEvent.click(await screen.findByLabelText("Exploratory"));
    await userEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try sending again" }));
    await waitFor(() => expect(posts(calls)).toHaveLength(2));
    const [first, second] = posts(calls).map((c) => JSON.parse(String(c.init?.body)));
    expect(second.text).toBe("Exploratory");
    expect(second.submission_id).toBe(first.submission_id); // a retry of the same answer, never doubled
  });

  it("does not offer the list while a reply is still being written", async () => {
    setup({
      "GET /api/conversation": () =>
        json({
          revision: 1,
          turns: [user(), bot("pending")],
          canvas: { fields: {}, missing_required: ["priority"], ready_for_review: false },
          progression: { phase: "interview", outstanding_question: { target: "priority", kind: "missing", text: "x", choices: PRIORITY } },
        }),
      [STREAM]: () => new Promise<Response>(() => {}),
    });
    await screen.findByText("My idea");
    expect(screen.queryByRole("group", { name: "Choose one" })).not.toBeInTheDocument();
  });
});
