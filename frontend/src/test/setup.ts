import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { setAccessToken } from "../auth/session";

afterEach(() => {
  cleanup();
  setAccessToken(null);
});
