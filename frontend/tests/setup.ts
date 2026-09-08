import "@testing-library/jest-dom/vitest";

// jsdom does not implement matchMedia, and the theme toggle reads it to fall
// back to the operating system's preference. Defaults to light; a test that
// cares about the dark default overrides this.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}
