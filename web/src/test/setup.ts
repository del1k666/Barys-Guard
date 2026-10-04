import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";

// Подмену fetch каждый тест ставит сам; не переносим её в следующий.
afterEach(() => {
  vi.unstubAllGlobals();
});
