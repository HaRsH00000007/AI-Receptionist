/**
 * Sign-in: the password form, the link fallback, and the page a link lands on.
 */

import { StrictMode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/LoginForm";
import { MagicLinkCallback } from "@/components/auth/MagicLinkCallback";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { SessionView } from "@/lib/types";

const replace = vi.fn();
const refresh = vi.fn();
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, refresh, push: vi.fn() }),
  useSearchParams: () => search,
}));

const SESSION: SessionView = {
  user_id: "u1",
  email: "owner@sunset.example",
  full_name: null,
  is_platform_admin: false,
  active_tenant_id: "t1",
  memberships: [],
  impersonated: false,
};

beforeEach(() => {
  replace.mockReset();
  refresh.mockReset();
  search = new URLSearchParams();
});

const PASSWORD = "a good long password";

/** The link form is behind a toggle now that the password is the default. */
function switchToLinkMode() {
  fireEvent.click(screen.getByRole("button", { name: /forgot your password/i }));
}

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("LoginForm", () => {
  it("can't be submitted without both an address and a password", () => {
    render(<LoginForm />);
    const submit = screen.getByRole("button", { name: /^sign in$/i });

    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    expect(submit).toBeDisabled();
  });

  it("signs in with a password and opens the dashboard", async () => {
    const signIn = vi.spyOn(api, "signInWithPassword").mockResolvedValue(SESSION);

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: " owner@sunset.example " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    // The address is trimmed; the password is not. A space someone typed on
    // purpose is part of their password, and trimming would lock them out of
    // an account they can otherwise open.
    expect(signIn).toHaveBeenCalledWith("owner@sunset.example", PASSWORD);
  });

  it("gives one answer for a wrong password and an unknown address", async () => {
    vi.spyOn(api, "signInWithPassword").mockRejectedValue(
      new ApiError("invalid email or password", { status: 401 }),
    );

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    // Never "no such account": that sentence is a free lookup for whether a
    // given business is a customer.
    expect(await screen.findByRole("alert")).toHaveTextContent(/don't match an account/i);
    expect(screen.getByRole("alert")).not.toHaveTextContent(/no such|not found|unknown/i);
    expect(replace).not.toHaveBeenCalled();
  });

  it("clears the password after a refusal", async () => {
    vi.spyOn(api, "signInWithPassword").mockRejectedValue(
      new ApiError("invalid email or password", { status: 401 }),
    );

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    await screen.findByRole("alert");
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("shows a rate limit as itself rather than as a wrong password", async () => {
    vi.spyOn(api, "signInWithPassword").mockRejectedValue(
      new ApiError("too many login attempts", { status: 429, code: "rate_limited" }),
    );

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    fireEvent.click(screen.getByRole("button", { name: /^sign in$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many login attempts/i);
  });

  it("offers the link as a way back in, and asks only for an address", () => {
    render(<LoginForm />);
    switchToLinkMode();

    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /email me a sign-in link/i })).toBeDisabled();
  });

  it("sends a link and confirms without revealing whether the account exists", async () => {
    const request = vi
      .spyOn(api, "requestMagicLink")
      .mockResolvedValue({ message: "If that address has an account, a sign-in link is on its way." });

    render(<LoginForm />);
    switchToLinkMode();
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: " owner@sunset.example " } });
    fireEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    expect(await screen.findByRole("heading", { name: /check your email/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/if owner@sunset.example has an account/i);
    expect(request).toHaveBeenCalledWith("owner@sunset.example");
  });

  it("reports a transport or rate-limit failure on the link path", async () => {
    vi.spyOn(api, "requestMagicLink").mockRejectedValue(
      new ApiError("too many login attempts", { status: 429, code: "rate_limited" }),
    );

    render(<LoginForm />);
    switchToLinkMode();
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    fireEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many login attempts/i);
  });

  it("does not carry a typed password across to the link form", () => {
    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: PASSWORD } });
    switchToLinkMode();
    fireEvent.click(screen.getByRole("button", { name: /sign in with a password instead/i }));

    expect(screen.getByLabelText("Password")).toHaveValue("");
  });
});

describe("MagicLinkCallback", () => {
  it("exchanges the token exactly once, strips it from the address, and opens the dashboard", async () => {
    // StrictMode runs effects twice. A single-use token exchanged twice would
    // fail the second time and show an error for a sign-in that worked.
    search = new URLSearchParams("token=single-use-token-0123456789");
    const exchange = vi.spyOn(api, "exchangeMagicLink").mockResolvedValue(SESSION);
    const replaceState = vi.spyOn(window.history, "replaceState");

    render(
      <StrictMode>
        <MagicLinkCallback />
      </StrictMode>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    expect(exchange).toHaveBeenCalledTimes(1);
    expect(exchange).toHaveBeenCalledWith("single-use-token-0123456789");
    expect(replaceState).toHaveBeenCalledWith(null, "", "/auth/callback");
  });

  it("explains an expired or used link and offers a new one", async () => {
    search = new URLSearchParams("token=expired-token-0123456789");
    vi.spyOn(api, "exchangeMagicLink").mockRejectedValue(
      new ApiError("invalid or expired login link", { status: 401 }),
    );

    render(<MagicLinkCallback />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/expired or has already been used/i);
    expect(screen.getByRole("link", { name: /send me a new link/i })).toHaveAttribute("href", "/login");
    expect(replace).not.toHaveBeenCalled();
  });

  it("does nothing without a token", () => {
    const exchange = vi.spyOn(api, "exchangeMagicLink");

    render(<MagicLinkCallback />);

    expect(screen.getByRole("alert")).toHaveTextContent(/needs the sign-in link/i);
    expect(exchange).not.toHaveBeenCalled();
  });
});
