import { useCallback, useEffect, useRef, useState } from "react";
import {
  type Conversation,
  type Phase,
  type Turn,
  getConversation,
  streamReply,
  submitTurn,
} from "../api/conversation";
import { ApiError } from "../api/client";
import { CanvasPanel } from "./CanvasPanel";
import { navigate, ROUTES } from "../router";
import { Loading } from "./Loading";

const MAX_ANSWER_CHARS = 4000;

// Fixed copy chosen by error code. Nothing from a response body is ever shown.
const SUBMIT_MESSAGES: Record<string, string> = {
  revision_conflict:
    "Your conversation has changed since you opened it, perhaps in another tab. Reload to see the latest, then send your answer again.",
  turn_in_progress: "The assistant has not finished replying to your last answer. Get that reply first.",
  conversation_full: "This conversation has reached its limit.",
};
const UNSURE_SAVED =
  "We could not confirm that your answer was saved. Try sending it again: it will not be added twice.";

// crypto.randomUUID exists in the secure contexts this app runs in (HTTPS, localhost).
const newSubmissionId = (): string => crypto.randomUUID();

// Fixed copy keyed on the server's phase: nothing from the response is rendered as text here.
const PHASE_LABELS: Record<Phase, string | null> = {
  interview: null,
  clarification: "Clarifying a detail",
  data_and_systems: "Checking data and systems",
  review_ready: "Ready to review",
};

// What the stored history says about the last assistant turn.
function unfinishedTurn(history: Conversation | null): Turn | null {
  const last = history?.turns.at(-1);
  return last && last.role === "assistant" && last.status !== "complete" ? last : null;
}

function AssistantAvatar({ small }: { small?: boolean }) {
  return (
    <span className={small ? "avatar avatar--assistant avatar--small" : "avatar avatar--assistant"} aria-hidden="true">
      <svg viewBox="0 0 24 24" width="60%" height="60%" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" />
        <path d="M8.5 10.5h.01M12 10.5h.01M15.5 10.5h.01" />
      </svg>
    </span>
  );
}

function UserAvatar() {
  return (
    <span className="avatar avatar--user" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="60%" height="60%" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 20c.8-4 3.5-6 7-6s6.2 2 7 6" />
      </svg>
    </span>
  );
}

type Reply =
  | { phase: "idle" }
  | { phase: "streaming"; turnId: string; text: string }
  | { phase: "interrupted" }
  | { phase: "failed" };

export function ConversationScreen({ onAccessLost }: { onAccessLost: () => void }) {
  const [history, setHistory] = useState<Conversation | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);
  const [reply, setReply] = useState<Reply>({ phase: "idle" });

  // One id per answer, kept across retries so the server can never record it twice.
  const submission = useRef<{ id: string; text: string } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const followNewest = useRef(true); // false once the user scrolls up to re-read earlier messages
  const streamAbort = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      streamAbort.current?.abort(); // leaving the screen (e.g. browser back) stops the stream
    };
  }, []);

  const handleError = useCallback(
    (error: unknown): ApiError => {
      const apiError = error instanceof ApiError ? error : new ApiError(null, "client_error", null);
      if (apiError.status === 401) onAccessLost(); // session expired or ended: neutral restart
      return apiError;
    },
    [onAccessLost],
  );

  // Authoritative state. Called on load, after every submit, and after any interrupted stream.
  const refresh = useCallback(async (): Promise<Conversation | null> => {
    try {
      const latest = await getConversation();
      if (mounted.current) {
        setHistory(latest);
        setLoadError(null);
      }
      return latest;
    } catch (error) {
      const apiError = handleError(error);
      if (mounted.current && apiError.status !== 401) setLoadError(apiError);
      return null;
    }
  }, [handleError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const startReply = useCallback(
    async (turnId: string) => {
      streamAbort.current?.abort();
      const controller = new AbortController();
      streamAbort.current = controller;
      setReply({ phase: "streaming", turnId, text: "" });

      let outcome: "complete" | "failed" | null = null;
      try {
        await streamReply(
          turnId,
          (event) => {
            if (controller.signal.aborted) return;
            if (event.type === "delta") {
              setReply((r) => (r.phase === "streaming" ? { ...r, text: r.text + event.text } : r));
            } else {
              outcome = event.type;
            }
          },
          controller.signal,
        );
      } catch (error) {
        if (controller.signal.aborted) return;
        if (handleError(error).status === 401) return;
        // A broken stream proves nothing about what was stored: fall through and re-read it.
      }
      if (controller.signal.aborted || !mounted.current) return;

      const latest = await refresh(); // the stored turn replaces the transient text
      if (controller.signal.aborted || !mounted.current) return;
      if (outcome === "complete" || !unfinishedTurn(latest)) setReply({ phase: "idle" });
      else setReply({ phase: outcome === "failed" ? "failed" : "interrupted" });
    },
    [handleError, refresh],
  );

  async function send(event?: { preventDefault(): void }) {
    event?.preventDefault();
    if (sending || !history || !draft.trim()) return;
    // Same text => same id (a retry). Edited text => a new answer, so a new id.
    if (!submission.current || submission.current.text !== draft) {
      submission.current = { id: newSubmissionId(), text: draft };
    }
    followNewest.current = true;
    setSending(true);
    setSaved(false);
    setSubmitError(null);
    try {
      const result = await submitTurn({
        submissionId: submission.current.id,
        expectedRevision: history.revision,
        text: draft,
      });
      submission.current = null;
      if (mounted.current) {
        setDraft("");
        setSaved(true);
      }
      await refresh();
      if (mounted.current && result.assistant_turn.status !== "complete") {
        void startReply(result.assistant_turn.turn_id);
      }
    } catch (error) {
      const apiError = handleError(error);
      if (mounted.current && apiError.status !== 401) setSubmitError(apiError);
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  function onScroll() {
    const el = scrollRef.current;
    if (el) followNewest.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  }

  async function reload() {
    setSubmitError(null);
    setReply({ phase: "idle" });
    await refresh();
  }

  // Keep the newest message in view as replies stream in, unless the user scrolled up.
  const shownCount = history?.turns.length ?? 0;
  const liveLength = reply.phase === "streaming" ? reply.text.length : -1;
  useEffect(() => {
    const el = scrollRef.current;
    if (el && followNewest.current) el.scrollTop = el.scrollHeight;
  }, [shownCount, liveLength, reply.phase]);

  if (!history && !loadError) return <Loading label="Loading your conversation…" />;
  if (!history && loadError) {
    return (
      <div role="alert" className="error-notice">
        <p>We could not load your conversation. Your answers are not lost.</p>
        {loadError.correlationId && (
          <p className="error-notice__reference">
            Reference: <code>{loadError.correlationId}</code>
          </p>
        )}
        <button type="button" onClick={() => void refresh()}>
          Reload conversation
        </button>
      </div>
    );
  }

  const turns = history?.turns ?? [];
  const unfinished = unfinishedTurn(history);
  const streaming = reply.phase === "streaming";
  const visibleTurns = turns.filter((t) => t.role === "user" || t.status === "complete");
  const firstAnswer = turns.length === 0;
  const blocked = sending || streaming || Boolean(unfinished);
  const submitMessage = submitError ? (SUBMIT_MESSAGES[submitError.code] ?? UNSURE_SAVED) : null;

  return (
    <div className="conversation-layout">
      <div className="conversation-main">
        <h1>Describe your idea</h1>
        {firstAnswer && (
          <p>
            Tell us about your idea in your own words. A rough description is fine: you do not need to fill in a
            template first.
          </p>
        )}

        <section className="chat" aria-label="Chat with the assistant">
          <header className="chat__header">
            <span className="chat__pill">
              <AssistantAvatar small />
              AI assistant
              <span className="chat__online" aria-hidden="true" />
            </span>
            {PHASE_LABELS[history?.progression?.phase ?? "interview"] && (
              <span className="chat__phase" data-testid="interview-phase">
                {PHASE_LABELS[history?.progression?.phase ?? "interview"]}
              </span>
            )}
          </header>

          <div className="chat__messages" ref={scrollRef} onScroll={onScroll}>
            {firstAnswer && (
              <p className="chat__empty" aria-hidden="true">
                Your conversation will appear here.
              </p>
            )}
            <ol className="transcript" aria-label="Conversation">
              {visibleTurns.map((turn) => (
                <li key={turn.turn_id} className={`turn turn--${turn.role}`}>
                  {turn.role === "user" ? <UserAvatar /> : <AssistantAvatar />}
                  <div className="turn__bubble">
                    <span className="turn__who">{turn.role === "user" ? "You said" : "Assistant said"}</span>
                    <p className="turn__text">{turn.text}</p>
                  </div>
                </li>
              ))}
              {reply.phase === "streaming" && (
                <li className="turn turn--assistant turn--pending" aria-busy="true">
                  <AssistantAvatar />
                  <div className="turn__bubble">
                    <span className="turn__who">Assistant, reply in progress (not yet confirmed)</span>
                    <p className="turn__text" data-testid="live-reply">
                      {reply.text}
                    </p>
                    {!reply.text && (
                      <span className="typing" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    )}
                  </div>
                </li>
              )}
            </ol>
          </div>

          <div className="chat__footer">
            {/* Two separate statuses: is my answer stored, and what is the assistant doing. */}
            <div role="status" className="statuses">
              <p data-testid="persistence-status">
                {sending ? "Saving your answer…" : saved || turns.length > 0 ? "Your answers are saved." : ""}
              </p>
              <p data-testid="reply-status">
                {streaming && "The assistant is replying…"}
                {reply.phase === "interrupted" && "The reply was interrupted. Your answer is saved."}
                {reply.phase === "failed" && "The assistant could not reply just now. Your answer is saved."}
                {reply.phase === "idle" && unfinished && "A reply is waiting for this answer."}
              </p>
            </div>

            {unfinished && !streaming && (
              <div className="actions">
                <button type="button" onClick={() => void startReply(unfinished.turn_id)}>
                  {reply.phase === "failed" || unfinished.status === "failed" ? "Try the reply again" : "Get the reply"}
                </button>
                <button type="button" className="button--secondary" onClick={() => void reload()}>
                  Reload conversation
                </button>
              </div>
            )}

            {submitError && (
              <div role="alert" className="error-notice">
                <p>{submitMessage}</p>
                {submitError.correlationId && (
                  <p className="error-notice__reference">
                    Reference: <code>{submitError.correlationId}</code>
                  </p>
                )}
                <div className="actions">
                  {submitError.code !== "revision_conflict" &&
                    submitError.code !== "turn_in_progress" &&
                    submitError.code !== "conversation_full" && (
                      <button type="button" onClick={() => void send()}>
                        Try sending again
                      </button>
                    )}
                  <button type="button" className="button--secondary" onClick={() => void reload()}>
                    Reload conversation
                  </button>
                </div>
              </div>
            )}

            <form onSubmit={(e) => void send(e)} className="answer-form composer">
              <label htmlFor="answer" className="sr-only">
                {firstAnswer ? "Describe your idea" : "Your answer"}
              </label>
              <textarea
                id="answer"
                name="answer"
                rows={2}
                maxLength={MAX_ANSWER_CHARS}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={firstAnswer ? "Describe your idea in your own words…" : "Type your answer…"}
                aria-describedby="answer-help"
              />
              <button type="submit" disabled={blocked || !draft.trim()}>
                {sending ? "Sending…" : "Send"}
              </button>
            </form>
            <p id="answer-help" className="field__help">
              Up to {MAX_ANSWER_CHARS} characters. Do not include passwords or personal details.
            </p>
          </div>
        </section>
      </div>
      <CanvasPanel canvas={history?.canvas} onGenerate={() => navigate(ROUTES.review)} />
    </div>
  );
}
