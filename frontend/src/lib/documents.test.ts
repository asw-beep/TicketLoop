import { describe, expect, it } from "vitest";
import { MAX_DOCUMENT_BYTES, checkFile } from "./documents";

describe("checkFile", () => {
  it.each([
    [{ size: 0, type: "image/png" }, /empty/i],
    [{ size: MAX_DOCUMENT_BYTES + 1, type: "application/pdf" }, /5 MB/],
    [{ size: 100, type: "text/plain" }, /PDF, PNG or JPEG/],
    [{ size: 100, type: "image/gif" }, /PDF, PNG or JPEG/],
  ])("refuses %j", (file, message) => {
    expect(checkFile(file)).toMatch(message);
  });

  it.each([
    { size: 1, type: "image/png" },
    { size: 2048, type: "image/jpeg" },
    { size: MAX_DOCUMENT_BYTES, type: "application/pdf" },
  ])("accepts %j", (file) => {
    expect(checkFile(file)).toBeNull();
  });

  it("leaves an unknown browser type to the server, which reads the bytes", () => {
    expect(checkFile({ size: 100, type: "" })).toBeNull();
  });

  it("matches the server's 5 MiB limit", () => {
    expect(MAX_DOCUMENT_BYTES).toBe(5_242_880);
  });
});
