import { apiRequest, apiStream } from "./client";

export interface Turn {
  turn_id: string;
  role: "user" | "assistant";
  text: string;
  status: "complete" | "pending" | "failed";
}

// The canvas draft the interviewer has built so far. Field ids match src/canvas/fields.ts.
export interface CanvasView {
  fields: Record<string, string | string[]>;
  missing_required: string[];
  ready_for_review: boolean;
}

// Where the guided interview is. Computed by the server; the page only shows it.
export type Phase = "interview" | "clarification" | "data_and_systems" | "review_ready";
// Set only when the answer is one (or several) of a fixed list. The server owns the list; the page
// shows whatever it is sent.
export interface Choices {
  multiple: boolean;
  options: string[];
}
export interface OutstandingQuestion {
  target: string;
  kind: string;
  text: string | null;
  choices: Choices | null;
}
export interface Progression {
  phase: Phase;
  outstanding_question?: OutstandingQuestion | null;
}

// The stored history is the source of truth. `revision` is the version the next answer builds on.
export interface Conversation {
  revision: number;
  turns: Turn[];
  canvas?: CanvasView;
  progression?: Progression;
  // The revision the user confirmed in the read-back, or null. It counts only while it equals
  // `revision`: any later answer moves `revision` on, which ends the confirmation.
  confirmed_revision?: number | null;
}

export interface Submission {
  revision: number;
  user_turn: Turn;
  assistant_turn: Turn;
}

export type ReplyEvent =
  | { type: "delta"; text: string }
  | { type: "complete" }
  | { type: "failed"; reason: string };

export function getConversation(signal?: AbortSignal): Promise<Conversation> {
  return apiRequest<Conversation>("/api/conversation", { signal });
}

// The user confirms the revision they read back. Returns the refreshed review.
export function confirmCanvas(revision: number, signal?: AbortSignal): Promise<Conversation> {
  return apiRequest<Conversation>("/api/canvas/confirm", { method: "POST", csrf: true, signal, body: { revision } });
}

// `submissionId` identifies this answer: reusing it on a retry can never record it twice.
export function submitTurn(
  input: { submissionId: string; expectedRevision: number; text: string },
  signal?: AbortSignal,
): Promise<Submission> {
  return apiRequest<Submission>("/api/conversation/turns", {
    method: "POST",
    csrf: true,
    signal,
    body: {
      submission_id: input.submissionId,
      expected_revision: input.expectedRevision,
      text: input.text,
    },
  });
}

function parseReplyEvent(raw: unknown): ReplyEvent | null {
  if (typeof raw !== "object" || raw === null) return null;
  const event = raw as { type?: unknown; text?: unknown; reason?: unknown };
  if (event.type === "delta" && typeof event.text === "string") return { type: "delta", text: event.text };
  if (event.type === "complete") return { type: "complete" };
  if (event.type === "failed") {
    return { type: "failed", reason: typeof event.reason === "string" ? event.reason : "unknown" };
  }
  return null; // unknown events are ignored, never rendered
}

// Streams progress for one assistant turn. Fragments are transient: the caller shows them as
// pending and replaces them with the stored turn once the server confirms completion.
export function streamReply(
  turnId: string,
  onEvent: (event: ReplyEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  if (!/^[a-f0-9]{32}$/.test(turnId)) throw new Error("invalid turn id"); // never build a URL from odd input
  return apiStream(
    `/api/conversation/turns/${turnId}/stream`,
    (raw) => {
      const event = parseReplyEvent(raw);
      if (event) onEvent(event);
    },
    signal,
  );
}
