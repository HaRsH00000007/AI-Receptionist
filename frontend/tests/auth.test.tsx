/**
 * Sign-in: requesting a link, and the page the emailed link lands on.
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
let search = new URLSearchParams();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
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
  search = new URLSearchParams();
});

afterEach(() => {
  vi.restoreAllMocks();
  cleanup();
});

describe("LoginForm", () => {
  it("can't be submitted without an address", () => {
    render(<LoginForm />);
    expect(screen.getByRole("button", { name: /email me a sign-in link/i })).toBeDisabled();
  });

  it("sends a link and confirms without revealing whether the account exists", async () => {
    const request = vi
      .spyOn(api, "requestMagicLink")
      .mockResolvedValue({ message: "If that address has an account, a sign-in link is on its way." });

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: " owner@sunset.example " } });
    fireEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    expect(await screen.findByRole("heading", { name: /check your email/i })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/if owner@sunset.example has an account/i);
    expect(request).toHaveBeenCalledWith("owner@sunset.example");
  });

  it("reports a transport or rate-limit failure", async () => {
    vi.spyOn(api, "requestMagicLink").mockRejectedValue(
      new ApiError("too many login attempts", { status: 429, code: "rate_limited" }),
    );

    render(<LoginForm />);
    fireEvent.change(screen.getByLabelText("Email address"), { target: { value: "owner@sunset.example" } });
    fireEvent.click(screen.getByRole("button", { name: /email me a sign-in link/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/too many login attempts/i);
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
