import { apiRequest, ApiError, errorFromResponse } from "./client";

// One PDF, Word, PowerPoint or JSON file can be attached to an answer. The server reads its text and
// throws the file away; the text then travels as part of the answer, exactly like typed text.
export const ATTACHMENT_ACCEPT = ".pdf,.docx,.pptx,.json";
export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
const EXTENSIONS = /\.(pdf|docx|pptx|json)$/i;

export interface Attachment {
  filename: string;
  kind: string;
  characters: number;
  truncated: boolean;
  // The text to append to the answer, already in the form the server and the AI expect.
  block: string;
}

function isAttachment(value: unknown): value is Attachment {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.filename === "string" &&
    typeof v.kind === "string" &&
    typeof v.characters === "number" &&
    typeof v.truncated === "boolean" &&
    typeof v.block === "string"
  );
}

export async function readAttachment(file: File, signal?: AbortSignal): Promise<Attachment> {
  if (!EXTENSIONS.test(file.name)) throw new ApiError(null, "unsupported_file_type", null);
  if (file.size > MAX_ATTACHMENT_BYTES) throw new ApiError(null, "file_too_large", null);
  const { csrf_token } = await apiRequest<{ csrf_token: string }>("/api/csrf", { signal });
  let response: Response;
  try {
    response = await fetch(`/api/attachments?name=${encodeURIComponent(file.name)}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRF-Token": csrf_token, Accept: "application/json" },
      body: file,
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(null, "network_error", null);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw errorFromResponse(response.status, payload, response.headers.get("X-Correlation-ID"));
  if (!isAttachment(payload)) throw new ApiError(response.status, "unexpected_response", null);
  return payload;
}

// What the user typed, and the files attached to an answer, so a long file does not flood the chat.
const BLOCK = /\[\[Attached file: ([^\n\]|]*) \| ([a-z]+)( \| shortened)?\]\]\n([\s\S]*?)\n\[\[End of attached file\]\]/g;

export interface AttachedFile {
  name: string;
  kind: string;
  shortened: boolean;
  text: string;
}

export function splitAttachments(answer: string): { typed: string; files: AttachedFile[] } {
  const files: AttachedFile[] = [];
  const typed = answer
    .replace(BLOCK, (_all, name: string, kind: string, shortened: string | undefined, text: string) => {
      files.push({ name, kind, shortened: Boolean(shortened), text });
      return "";
    })
    .trim();
  return { typed, files };
}
