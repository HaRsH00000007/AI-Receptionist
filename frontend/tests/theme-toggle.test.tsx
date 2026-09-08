/**
 * The light/dark switch.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY, ThemeToggle } from "@/app/ThemeToggle";

function withSystemPreference(prefersDark: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: prefersDark,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  withSystemPreference(false);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ThemeToggle", () => {
  it("offers dark mode when the page is light", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeInTheDocument();
  });

  it("switches to dark and remembers the choice", () => {
    render(<ThemeToggle />);

    fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });

  it("switches back to light", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button");

    fireEvent.click(button); // -> dark
    fireEvent.click(button); // -> light

    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("follows the operating system when nothing is stored", () => {
    withSystemPreference(true);
    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument();
  });

  it("prefers an explicit choice over the operating system", () => {
    withSystemPreference(true);
    localStorage.setItem(THEME_STORAGE_KEY, "light");

    render(<ThemeToggle />);

    expect(screen.getByRole("button", { name: "Switch to dark mode" })).toBeInTheDocument();
  });

  it("still works when storage is unavailable", () => {
    // Private browsing throws on both read and write. The toggle must apply the
    // theme anyway; it just cannot remember it.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button"));

    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});
