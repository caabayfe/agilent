import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom does not implement scrolling.
Element.prototype.scrollIntoView = vi.fn();
