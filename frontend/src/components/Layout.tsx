import type { ReactNode } from "react";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="layout">
      <header className="layout__header">
        <span className="layout__brand">BHP</span>
      </header>
      <main className="layout__main">{children}</main>
      <footer className="layout__footer">BHP</footer>
    </div>
  );
}
