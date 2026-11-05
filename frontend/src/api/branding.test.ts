import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_BRANDING, getBranding } from "./branding";
import { json, mockApi } from "../test/mockApi";

afterEach(() => vi.unstubAllGlobals());

describe("getBranding", () => {
  it("returns the configured names", async () => {
    mockApi({ "GET /api/branding": () => json({ app_name: "Integrated Operations", header_text: "IO", footer_text: "BHP · IO" }) });
    expect(await getBranding()).toEqual({ appName: "Integrated Operations", headerText: "IO", footerText: "BHP · IO" });
  });

  it("falls back to the defaults for anything missing, empty, not text or too long", async () => {
    mockApi({ "GET /api/branding": () => json({ app_name: "   ", header_text: 5, footer_text: "x".repeat(81) }) });
    expect(await getBranding()).toEqual(DEFAULT_BRANDING);
  });

  it("trims the names", async () => {
    mockApi({ "GET /api/branding": () => json({ app_name: "  Ops  ", header_text: " Ops ", footer_text: " BHP " }) });
    expect(await getBranding()).toEqual({ appName: "Ops", headerText: "Ops", footerText: "BHP" });
  });
});
