import { useCallback, useEffect, useRef, useState } from "react";
import { getSession, keepSession } from "../api/access";
import { ApiError } from "../api/client";

const WARN_AT_S = 120; // warn this long before the session ends for want of activity
const RECHECK_MS = 20_000; // re-read the server's idea of the deadline (this does not count as activity)
// Someone typing, clicking, scrolling or moving the mouse is not idle. The server only hears about
// answers and downloads, so real use of the page is reported too, at most this often.
const PRESENT_EVERY_MS = 60_000;
const PRESENCE_EVENTS = ["keydown", "pointerdown", "pointermove", "wheel", "touchstart"] as const;

const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

// A session with no activity for 15 minutes ends on the server. This watches that deadline: it says
// nothing until two minutes before, then warns with a button to stay, and when time is up returns to
// the entry page. The server is the authority; this only shows it, and re-reads it now and then
// (those reads are not activity, so an open tab alone never keeps a session alive). A person at the
// page (typing a long answer, reading, scrolling) is active: that is reported once a minute at most.
export function IdleWatch({ remaining, onEnded }: { remaining: number; onEnded: () => void }) {
  const deadline = useRef(Date.now() + remaining * 1000);
  const [left, setLeft] = useState(remaining);
  const [staying, setStaying] = useState(false);
  const ended = useRef(false);

  const end = useCallback(() => {
    if (ended.current) return;
    ended.current = true;
    onEnded();
  }, [onEnded]);

  const adopt = useCallback((seconds: number | undefined) => {
    if (typeof seconds !== "number") return;
    deadline.current = Date.now() + seconds * 1000;
    setLeft(seconds);
  }, []);

  useEffect(() => {
    const tick = window.setInterval(() => {
      const seconds = Math.max(0, Math.ceil((deadline.current - Date.now()) / 1000));
      setLeft(seconds);
      if (seconds <= 0) end();
    }, 1000);
    const recheck = window.setInterval(() => {
      getSession()
        .then((info) => adopt(info.idle_seconds_remaining))
        .catch((error) => {
          if (error instanceof ApiError && error.status === 401) end();
        });
    }, RECHECK_MS);
    return () => {
      window.clearInterval(tick);
      window.clearInterval(recheck);
    };
  }, [adopt, end]);

  const lastPresent = useRef(Date.now());
  useEffect(() => {
    const present = () => {
      const now = Date.now();
      if (ended.current || now - lastPresent.current < PRESENT_EVERY_MS) return;
      lastPresent.current = now;
      keepSession()
        .then((info) => adopt(info.idle_seconds_remaining))
        .catch((error) => {
          if (error instanceof ApiError && error.status === 401) end();
        });
    };
    for (const name of PRESENCE_EVENTS) window.addEventListener(name, present, { passive: true });
    return () => {
      for (const name of PRESENCE_EVENTS) window.removeEventListener(name, present);
    };
  }, [adopt, end]);

  const stay = async () => {
    setStaying(true);
    try {
      adopt((await keepSession()).idle_seconds_remaining);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) end();
    } finally {
      setStaying(false);
    }
  };

  if (left > WARN_AT_S) return null;
  return (
    <div className="idle-warning">
      <p role="alert">Your session is about to end because nothing has happened for a while. Everything in it will be deleted.</p>
      <p className="idle-warning__row">
        <span>Time left: {clock(left)}</span>
        <button type="button" onClick={() => void stay()} disabled={staying}>
          {staying ? "Keeping…" : "Keep my session"}
        </button>
      </p>
    </div>
  );
}
