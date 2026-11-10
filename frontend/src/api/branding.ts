import { apiRequest } from "./client";

// What the page calls itself. The server reads these from configuration, so the name can change
// with a restart and no rebuild. Shown as plain text only.
export interface Branding {
  appName: string;
  headerText: string;
  footerText: string;
}

export const DEFAULT_BRANDING: Branding = {
  appName: "SCP AI Ideation assistant",
  headerText: "SCP AI Ideation assistant",
  footerText: "BHP",
};

const MAX_CHARS = 80;
const plain = (value: unknown, fallback: string) =>
  typeof value === "string" && value.trim() !== "" && value.length <= MAX_CHARS ? value.trim() : fallback;

// Anything missing or odd falls back to the defaults, so a bad response can never blank the header.
export async function getBranding(signal?: AbortSignal): Promise<Branding> {
  const body = await apiRequest<Record<string, unknown>>("/api/branding", { signal });
  return {
    appName: plain(body?.app_name, DEFAULT_BRANDING.appName),
    headerText: plain(body?.header_text, DEFAULT_BRANDING.headerText),
    footerText: plain(body?.footer_text, DEFAULT_BRANDING.footerText),
  };
}
