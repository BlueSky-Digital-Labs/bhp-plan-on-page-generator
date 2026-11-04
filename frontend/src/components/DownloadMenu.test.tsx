import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DownloadMenu } from "./DownloadMenu";

const CHOICES = [
  { key: "pdf", label: "PDF" },
  { key: "docx", label: "Word (.docx)" },
  { key: "json", label: "JSON" },
];

function setup(busy = false) {
  const onChoose = vi.fn();
  render(
    <>
      <DownloadMenu label="Download appendix" choices={CHOICES} busy={busy} onChoose={onChoose} />
      <button type="button">elsewhere</button>
    </>,
  );
  return { onChoose, trigger: screen.getByRole("button", { name: /Download appendix|Preparing/ }) };
}

describe("DownloadMenu", () => {
  it("is one closed button until it is opened, then lists the formats", async () => {
    const { trigger } = setup();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("menuitem").map((el) => el.textContent)).toEqual(["PDF", "Word (.docx)", "JSON"]);
  });

  it("starts the download for the chosen format and closes", async () => {
    const { trigger, onChoose } = setup();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("menuitem", { name: "Word (.docx)" }));
    expect(onChoose).toHaveBeenCalledExactlyOnceWith("docx");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("works from the keyboard: arrow keys move, Enter chooses", async () => {
    const { trigger, onChoose } = setup();
    trigger.focus();
    await userEvent.keyboard("{ArrowDown}");
    await waitFor(() => expect(screen.getByRole("menuitem", { name: "PDF" })).toHaveFocus());
    await userEvent.keyboard("{ArrowDown}{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "JSON" })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}"); // wraps round
    expect(screen.getByRole("menuitem", { name: "PDF" })).toHaveFocus();
    await userEvent.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "JSON" })).toHaveFocus();
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("menuitem", { name: "PDF" })).toHaveFocus();
    await userEvent.keyboard("{End}{Enter}");
    expect(onChoose).toHaveBeenCalledExactlyOnceWith("json");
  });

  it("Escape closes it and returns to the button without choosing", async () => {
    const { trigger, onChoose } = setup();
    await userEvent.click(trigger);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("closes when the user clicks elsewhere, and does nothing else", async () => {
    const { trigger, onChoose } = setup();
    await userEvent.click(trigger);
    await userEvent.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(onChoose).not.toHaveBeenCalled();
  });

  it("clicking the button again closes it", async () => {
    const { trigger } = setup();
    await userEvent.click(trigger);
    await userEvent.click(trigger);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("cannot be opened while a download is being prepared", async () => {
    const { trigger } = setup(true);
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent("Preparing…");
    await userEvent.click(trigger);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
