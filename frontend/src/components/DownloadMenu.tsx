import { useEffect, useId, useRef, useState } from "react";

export interface MenuChoice {
  key: string;
  label: string;
}

// One button that opens a short list of formats; choosing one starts the download. A menu button:
// the arrow keys move through the choices, Escape closes it and returns to the button, and
// clicking elsewhere closes it.
export function DownloadMenu({
  label,
  choices,
  busy,
  onChoose,
  secondary = false,
}: {
  label: string;
  choices: MenuChoice[];
  busy: boolean;
  onChoose: (key: string) => void;
  secondary?: boolean; // the quieter, outlined look, for an optional action
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = useRef<Array<HTMLButtonElement | null>>([]);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const focusItem = (index: number) => {
    const last = choices.length - 1;
    items.current[index < 0 ? last : index > last ? 0 : index]?.focus();
  };

  const openAt = (index: number) => {
    setOpen(true);
    setTimeout(() => focusItem(index), 0); // after the list has rendered
  };

  const close = (returnFocus: boolean) => {
    setOpen(false);
    if (returnFocus) trigger.current?.focus();
  };

  const onTriggerKey = (event: React.KeyboardEvent) => {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      close(true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      openAt(0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openAt(-1);
    }
  };

  const onMenuKey = (event: React.KeyboardEvent) => {
    const index = items.current.findIndex((el) => el === document.activeElement);
    if (event.key === "ArrowDown") focusItem(index + 1);
    else if (event.key === "ArrowUp") focusItem(index - 1);
    else if (event.key === "Home") focusItem(0);
    else if (event.key === "End") focusItem(-1);
    else if (event.key === "Escape") close(true);
    else if (event.key === "Tab") close(false);
    else return;
    if (event.key !== "Tab") event.preventDefault();
  };

  return (
    <div className="menu" ref={root}>
      <button
        type="button"
        className={secondary ? "button--secondary" : undefined}
        ref={trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={busy}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onTriggerKey}
      >
        {busy ? "Preparing…" : label}
        {!busy && <span aria-hidden="true"> ▾</span>}
      </button>
      {open && (
        <div id={menuId} role="menu" aria-label={label} className="menu__list" onKeyDown={onMenuKey}>
          {choices.map((choice, index) => (
            <button
              key={choice.key}
              type="button"
              role="menuitem"
              className="menu__item"
              ref={(el) => {
                items.current[index] = el;
              }}
              onClick={() => {
                close(true);
                onChoose(choice.key);
              }}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
