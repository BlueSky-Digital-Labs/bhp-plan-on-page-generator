import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SESSION, csrfRoute, envelope, json, mockApi } from "../test/mockApi";
import { AccessGate } from "./AccessGate";

const CODE = "my-secret-code-987";

afterEach(() => vi.unstubAllGlobals());

async function submit(code = CODE) {
  await userEvent.type(screen.getByLabelText("Access code"), code);
  await userEvent.click(screen.getByRole("button", { name: "Continue" }));
}

describe("AccessGate", () => {
  it("hides the code while typing and disables submit until something is entered", () => {
    mockApi({});
    render(<AccessGate onSignedIn={vi.fn()} />);
    const input = screen.getByLabelText("Access code");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  });

  it("submits the code as a CSRF-protected form, then starts a session", async () => {
    const { calls } = mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => json({ status: "ok" }),
      "POST /api/sessions": () => json(SESSION, 201),
    });
    const onSignedIn = vi.fn();
    render(<AccessGate onSignedIn={onSignedIn} />);
    await submit();

    expect(onSignedIn).toHaveBeenCalledWith(SESSION);
    const keys = calls.map((c) => c.key);
    expect(keys.indexOf("POST /api/access")).toBeLessThan(keys.indexOf("POST /api/sessions"));

    const access = calls.find((c) => c.key === "POST /api/access")!;
    expect(String(access.init?.body)).toBe(`access_code=${CODE}`);
    const headers = access.init?.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("test-csrf-token");
    expect(headers["Content-Type"]).toBeUndefined(); // the browser sets the form content type
    const sessions = calls.find((c) => c.key === "POST /api/sessions")!;
    expect((sessions.init?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("test-csrf-token");
  });

  it("says the code was not recognised, without server text, and clears the field", async () => {
    mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => envelope("invalid_access_code", 401),
    });
    const onSignedIn = vi.fn();
    render(<AccessGate onSignedIn={onSignedIn} />);
    await submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("That access code was not recognised.");
    expect(onSignedIn).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Access code")).toHaveValue("");
    expect(screen.getByLabelText("Access code")).toHaveAttribute("aria-invalid", "true");
    expect(document.body).not.toHaveTextContent("SERVER-TEXT-MUST-NEVER-RENDER");
    expect(document.body).not.toHaveTextContent(CODE);
  });

  it("shows retry guidance when the service is unavailable (fails closed)", async () => {
    mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => envelope("service_unavailable", 503),
    });
    const onSignedIn = vi.fn();
    render(<AccessGate onSignedIn={onSignedIn} />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("falls back to the generic notice with a reference for anything unexpected", async () => {
    mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => envelope("request_not_verified", 403, "abcdefabcdefabcdefabcdefabcdefab"),
    });
    render(<AccessGate onSignedIn={vi.fn()} />);
    await submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong. Please try again.");
    expect(alert).toHaveTextContent("abcdefabcdefabcdefabcdefabcdefab");
  });

  it("does not start a session when the code step fails", async () => {
    const { calls } = mockApi({
      "GET /api/csrf": csrfRoute,
      "POST /api/access": () => envelope("invalid_access_code", 401),
    });
    render(<AccessGate onSignedIn={vi.fn()} />);
    await submit();
    await screen.findByRole("alert");
    expect(calls.some((c) => c.key === "POST /api/sessions")).toBe(false);
  });
});
