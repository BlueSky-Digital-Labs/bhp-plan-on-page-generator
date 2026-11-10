import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { type Branding, DEFAULT_BRANDING, getBranding } from "../api/branding";

const BrandingContext = createContext<Branding>(DEFAULT_BRANDING);

export const useBranding = () => useContext(BrandingContext);

// Reads the configured names once, before the page is drawn, so the header never shows one name
// and then switches to another. If they cannot be read, the defaults are used: the app still works.
export function BrandingProvider({ children }: { children: ReactNode }) {
  const [branding, setBranding] = useState<Branding | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getBranding(controller.signal)
      .then((value) => setBranding(value))
      .catch(() => {
        if (!controller.signal.aborted) setBranding(DEFAULT_BRANDING);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (branding) document.title = branding.appName;
  }, [branding]);

  if (!branding) return null;
  return <BrandingContext.Provider value={branding}>{children}</BrandingContext.Provider>;
}
