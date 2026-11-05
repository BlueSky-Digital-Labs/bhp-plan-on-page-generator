import { apiRequest } from "./client";

// The server's own decision, from its own records. Nothing here is computed in the browser.
export interface DownloadStatus {
  revision: number;
  canvas_downloaded: boolean;
  appendix_downloaded: boolean;
  purge_eligible: boolean;
  lock_reason: string | null;
}

// Which of the two PDFs the server has finished sending for the current revision.
export function getDownloadStatus(signal?: AbortSignal): Promise<DownloadStatus> {
  return apiRequest<DownloadStatus>("/api/downloads", { signal });
}

// Deletes this session's conversation, canvas and documents, if the server agrees right now.
// `revision` is the one the user was shown; the server decides eligibility on its own.
export function purgeSession(revision: number, signal?: AbortSignal): Promise<void> {
  return apiRequest<void>("/api/session/purge", { method: "POST", csrf: true, noContent: true, signal, body: { revision } });
}
