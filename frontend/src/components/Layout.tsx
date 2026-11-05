import type { ReactNode } from "react";
import { useBranding } from "../hooks/useBranding";

export function Layout({ children }: { children: ReactNode }) {
  const { headerText, footerText } = useBranding();
  return (
    <div className="layout">
      <header className="layout__header">
        <img className="layout__logo" src="/bhp-logo.png" alt="BHP" width="64" height="30" />
        <span className="layout__brand">{headerText}</span>
      </header>
      <main className="layout__main">{children}</main>
      <footer className="layout__footer">{footerText}</footer>
    </div>
  );
}
