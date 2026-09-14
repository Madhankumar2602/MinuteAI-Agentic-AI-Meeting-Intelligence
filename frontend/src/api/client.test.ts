import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, configureApiClient, errorMessage } from "./client";

function respond(status: number, body: unknown, headers: Record<string, string> = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(status === 204 ? null : JSON.stringify(body), { status, headers })),
  );
}

afterEach(() => configureApiClient({ getToken: () => null, onUnauthorized: () => undefined }));

describe("apiRequest", () => {
  it("attaches the bearer token and JSON body", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    configureApiClient({ getToken: () => "abc", onUnauthorized: () => undefined });

    await apiRequest("/meetings", { method: "POST", json: { title: "x" } });

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/v1/meetings");
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"title":"x"}');
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer abc");
  });

  it("does not attach a token when auth is false", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    configureApiClient({ getToken: () => "abc", onUnauthorized: () => undefined });

    await apiRequest("/auth/login", { method: "POST", form: { username: "a", password: "b" }, auth: false });

    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(init.body).toBe("username=a&password=b");
  });

  it("turns the error envelope into an ApiError with code and request id", async () => {
    respond(409, { error: { code: "transcript_missing", message: "Add a transcript first.", request_id: "rid-1" } });

    const error = await apiRequest("/meetings/x/process", { method: "POST" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: "transcript_missing", message: "Add a transcript first.", requestId: "rid-1" });
  });

  it("handles non-envelope failures without crashing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>bad gateway</html>", { status: 502, headers: { "X-Request-ID": "rid-2" } })));

    const error = (await apiRequest("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.code).toBe("http_error");
    expect(error.requestId).toBe("rid-2");
  });

  it("signs out on 401 when a token was sent, but not for anonymous requests", async () => {
    const onUnauthorized = vi.fn();
    respond(401, { error: { code: "unauthorized", message: "Token has expired." } });

    configureApiClient({ getToken: () => "expired", onUnauthorized });
    await apiRequest("/auth/me").catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    configureApiClient({ getToken: () => null, onUnauthorized });
    await apiRequest("/auth/login", { method: "POST", auth: false }).catch(() => undefined);
    expect(onUnauthorized).toHaveBeenCalledTimes(1); // wrong password is not a session expiry
  });

  it("reports an unreachable server as a network error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));

    const error = (await apiRequest("/x").catch((e: unknown) => e)) as ApiError;

    expect(error.code).toBe("network_error");
  });

  it("returns undefined for 204 No Content", async () => {
    respond(204, null);
    await expect(apiRequest("/meetings/x", { method: "DELETE" })).resolves.toBeUndefined();
  });
});

describe("errorMessage", () => {
  it("names the first invalid field of a validation error", () => {
    const error = new ApiError(422, "validation_error", "Request validation failed.", null, [
      { field: "body.password", message: "String should have at least 8 characters" },
    ]);
    expect(errorMessage(error)).toBe("password: String should have at least 8 characters");
  });

  it("falls back sensibly for unknown values", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
    expect(errorMessage("weird")).toBe("Something went wrong.");
  });
});
