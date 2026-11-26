import { describe, expect, it, vi } from "vitest";
import { getAccessToken, setAccessToken } from "../auth/session";
import { api, authFetch, unwrap } from "./client";

const PROBLEM = "application/problem+json";

function problem(status: number, code: string): Response {
  return new Response(
    JSON.stringify({ type: `urn:ticketloop:problem:${code}`, title: code, status }),
    { status, headers: { "Content-Type": PROBLEM } },
  );
}

function ok(body: unknown = { ok: true }): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** fetch as the server would answer it: each call takes the next response. */
function serverAnswers(...responses: Response[]) {
  const seen: Request[] = [];
  const mock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    if (input instanceof Request) seen.push(input);
    else seen.push(new Request(new URL(String(input), "http://localhost"), { method: "POST" }));
    const next = responses.shift();
    if (!next) throw new Error("unexpected extra request");
    return next;
  });
  return { seen, mock };
}

const ME = () => new Request("http://localhost/api/v1/users/me");

describe("authFetch", () => {
  it("sends the access token as a Bearer header", async () => {
    setAccessToken("abc");
    const { seen } = serverAnswers(ok());

    await authFetch(ME());

    expect(seen[0]!.headers.get("Authorization")).toBe("Bearer abc");
  });

  it("sends no Authorization header without a token", async () => {
    const { seen } = serverAnswers(ok());

    await authFetch(ME());

    expect(seen[0]!.headers.has("Authorization")).toBe(false);
  });

  it("refreshes once and retries when the access token has expired", async () => {
    setAccessToken("expired");
    const { seen } = serverAnswers(
      problem(401, "not-authenticated"),
      ok({ access_token: "fresh", expires_in: 900 }),
      ok({ id: "me" }),
    );

    const response = await authFetch(ME());

    expect(await response.json()).toEqual({ id: "me" });
    expect(seen.map((request) => new URL(request.url).pathname)).toEqual([
      "/api/v1/users/me",
      "/api/v1/auth/refresh",
      "/api/v1/users/me",
    ]);
    expect(seen[2]!.headers.get("Authorization")).toBe("Bearer fresh");
  });

  it("resends the original body on the retry", async () => {
    setAccessToken("expired");
    const { seen } = serverAnswers(
      problem(401, "not-authenticated"),
      ok({ access_token: "fresh", expires_in: 900 }),
      ok(),
    );
    const patch = new Request("http://localhost/api/v1/users/me", {
      method: "PATCH",
      body: JSON.stringify({ full_name: "Asha" }),
      headers: { "Content-Type": "application/json" },
    });

    await authFetch(patch);

    expect(await seen[2]!.json()).toEqual({ full_name: "Asha" });
  });

  it("gives back the 401 and forgets the token when the session has ended", async () => {
    setAccessToken("expired");
    serverAnswers(problem(401, "not-authenticated"), problem(401, "invalid-refresh-token"));

    const response = await authFetch(ME());

    expect(response.status).toBe(401);
    expect(getAccessToken()).toBeNull();
  });

  it("does not refresh on a 401 that is about something else, like a wrong password", async () => {
    setAccessToken("good");
    const { mock } = serverAnswers(problem(401, "wrong-password"));

    const response = await authFetch(
      new Request("http://localhost/api/v1/users/me/password", { method: "POST", body: "{}" }),
    );

    expect(response.status).toBe(401);
    expect(mock).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBe("good");
  });

  it("does not refresh when the request carried no token", async () => {
    // A 401 from login means wrong credentials, not an expired session.
    const { mock } = serverAnswers(problem(401, "not-authenticated"));

    await authFetch(ME());

    expect(mock).toHaveBeenCalledTimes(1);
  });
});

describe("unwrap", () => {
  it("throws the API's own problem, not a generic error", async () => {
    setAccessToken("good");
    serverAnswers(problem(401, "wrong-password"));

    const call = unwrap(
      api.POST("/api/v1/users/me/password", {
        body: { current_password: "not it at all", new_password: "a long new passphrase" },
      }),
    );

    await expect(call).rejects.toMatchObject({ code: "wrong-password", message: "wrong-password" });
  });

  it("keeps how long a 429 says to wait", async () => {
    const limited = problem(429, "rate-limited");
    limited.headers.set("Retry-After", "12");
    serverAnswers(limited);

    const call = unwrap(
      api.POST("/api/v1/auth/login", { body: { email: "a@b.co", password: "x" } }),
    );

    await expect(call).rejects.toMatchObject({ code: "rate-limited", retryAfter: 12 });
  });

  it("returns the data on success", async () => {
    setAccessToken("good");
    serverAnswers(ok({ id: "u1" }));

    await expect(unwrap(api.GET("/api/v1/users/me"))).resolves.toEqual({ id: "u1" });
  });
});
