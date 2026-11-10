import { useEffect, useId, useRef } from "react";

// A confirmation in a window of its own, over the page. It uses the browser's own modal dialog, so the
// page behind is inert, Escape closes it and focus returns to the button that opened it. Focus starts on
// the safe choice, not on the one that deletes things.
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  cancelLabel,
  workingLabel,
  working,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel: string;
  workingLabel: string;
  working: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const bodyId = useId();

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (typeof element.showModal === "function") element.showModal();
    else element.setAttribute("open", ""); // an environment without modal dialogs: still shown
    cancel.current?.focus();
    return () => {
      if (typeof element.close === "function") element.close();
      else element.removeAttribute("open");
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-labelledby={titleId}
      aria-describedby={bodyId}
      onCancel={(event) => {
        event.preventDefault(); // Escape: we close it ourselves, and not while a request is under way
        if (!working) onCancel();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      <p id={bodyId}>{body}</p>
      <div className="modal__actions">
        <button type="button" className="button--secondary" ref={cancel} onClick={onCancel} disabled={working}>
          {cancelLabel}
        </button>
        <button type="button" className="button--danger" onClick={onConfirm} disabled={working}>
          {working ? workingLabel : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
