import { ApiError, errorFromResponse } from "./client";

export type AppendixFormat = "pdf" | "docx" | "json";
export type CanvasFormat = "pdf" | "pptx";

// A file name is only used if it is the plain kind the server makes (lower-case letters, digits and
// hyphens, a known extension). Anything else falls back to a fixed name.
const SAFE_NAME = /^[a-z0-9][a-z0-9-]{0,100}\.(pdf|pptx|docx|json)$/;

function serverFilename(response: Response, fallback: string): string {
  const match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") ?? "");
  return match && SAFE_NAME.test(match[1]) ? match[1] : fallback;
}

// Saves a file the server builds on request for the caller's own session and the confirmed
// `revision`. Nothing is kept in the browser.
async function saveFile(path: string, fallbackName: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin" });
  } catch {
    throw new ApiError(null, "network_error", null);
  }
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    throw errorFromResponse(response.status, payload, response.headers.get("X-Correlation-ID"));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = serverFilename(response, fallbackName);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// The full answers and the conversation record behind the canvas.
export const downloadAppendix = (format: AppendixFormat, revision: number) =>
  saveFile(`/api/appendix?format=${format}&revision=${revision}`, `plan-on-a-page-appendix.${format}`);

// The one-page canvas, as a PDF or an editable PowerPoint slide.
export const downloadCanvas = (format: CanvasFormat, revision: number) =>
  saveFile(`/api/canvas-file?format=${format}&revision=${revision}`, `plan-on-a-page.${format}`);
