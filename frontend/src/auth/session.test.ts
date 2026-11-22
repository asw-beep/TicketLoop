import { describe, expect, it, vi } from "vitest";
import { getAccessToken, onAccessTokenChange, refreshAccessToken, setAccessToken } from "./session";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("refreshAccessToken", () => {
  it("swaps the cookie for an access token and keeps it in memory", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(json(200, { access_token: "new", token_type: "bearer", expires_in: 900 }));

    await expect(refreshAccessToken()).resolves.toBe("new");

    expect(getAccessToken()).toBe("new");
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/auth/refresh");
    expect(init).toMatchObject({ method: "POST", credentials: "same-origin" });
  });

  it("sends one request when many callers need a token at once", async () => {
    // Two refreshes with one cookie look like a stolen token to the server, which then
    // ends the session. So concurrent callers share one request.
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(json(200, { access_token: "new", token_type: "bearer", expires_in: 900 }));

    const tokens = await Promise.all([refreshAccessToken(), refreshAccessToken()]);

    expect(tokens).toEqual(["new", "new"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refreshes again once the previous refresh has finished", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () => json(200, { access_token: "t", expires_in: 900 }));

    await refreshAccessToken();
    await refreshAccessToken();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([401, 403])("treats %i as no session and forgets the token", async (status) => {
    setAccessToken("old");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json(status, { type: "x", title: "x" }));

    await expect(refreshAccessToken()).resolves.toBeNull();

    expect(getAccessToken()).toBeNull();
  });

  it("throws when the server fails, so the page can say the API is down", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("Bad gateway", { status: 502 }));

    await expect(refreshAccessToken()).rejects.toThrow("502");
  });
});

describe("onAccessTokenChange", () => {
  it("tells listeners about every change until they unsubscribe", () => {
    const seen: (string | null)[] = [];
    const unsubscribe = onAccessTokenChange((token) => seen.push(token));

    setAccessToken("a");
    setAccessToken(null);
    unsubscribe();
    setAccessToken("b");

    expect(seen).toEqual(["a", null]);
  });
});
