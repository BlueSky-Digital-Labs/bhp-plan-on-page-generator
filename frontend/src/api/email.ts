import { apiRequest } from "./client";

// The server builds the one-page canvas and the appendix as PDFs for the confirmed `revision` and
// emails both to the address and with the text the administrator set. Nothing else is sent: the
// browser cannot choose a recipient or add text, and nothing about the email is kept here.
export function submitCanvasEmail(revision: number, signal?: AbortSignal) {
  return apiRequest<{ sent: number }>("/api/email", {
    method: "POST",
    csrf: true,
    signal,
    body: { revision },
  });
}
