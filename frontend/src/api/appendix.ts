import { ApiError, errorFromResponse } from "./client";

export type AppendixFormat = "pdf" | "docx" | "json";

// Saves a file the server builds on request from what it has stored, for the caller's own session.
// The file name is fixed here, never taken from the response; nothing is kept in the browser.
async function saveFile(path: string, filename: string): Promise<void> {
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
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// The full answers and the conversation record behind the canvas.
export const downloadAppendix = (format: AppendixFormat) =>
  saveFile(`/api/appendix?format=${format}`, `plan-on-a-page-appendix.${format}`);

// The one-page canvas as an editable PowerPoint slide.
export const downloadCanvasPptx = () => saveFile("/api/canvas-file?format=pptx", "plan-on-a-page.pptx");
