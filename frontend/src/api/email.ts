import { apiRequest } from "./client";

export const MAX_RECIPIENTS = 5;
export const MAX_NOTE_CHARS = 2000;

// The addresses typed into the "To" box: separated by commas, semicolons, spaces or new lines, with
// repeats dropped. The server checks every one again; this only tells the user quickly.
export function parseRecipients(text: string): string[] {
  const seen = new Map<string, string>();
  for (const part of text.split(/[\s,;]+/)) {
    if (part) seen.set(part.toLowerCase(), seen.get(part.toLowerCase()) ?? part);
  }
  return [...seen.values()];
}

const SHAPE = /^[^\s@<>"',;]+@[^\s@<>"',;]+\.[^\s@<>"',;]{2,}$/;
export const looksLikeAddress = (value: string) => SHAPE.test(value);

// The server builds the one-page canvas and the appendix as PDFs for the confirmed `revision` and
// emails both. Nothing about the email is kept in the browser.
export function sendCanvasEmail(input: { revision: number; to: string[]; message: string }, signal?: AbortSignal) {
  return apiRequest<{ sent: number }>("/api/email", {
    method: "POST",
    csrf: true,
    signal,
    body: { revision: input.revision, to: input.to, message: input.message },
  });
}
