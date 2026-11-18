import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, errorMessage } from "@/services/api";
import { useAuth } from "@/stores/auth";

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("api client", () => {
  afterEach(() => { vi.restoreAllMocks(); useAuth.getState().logout(); });

  it("attaches bearer token and prefixes the API base", async () => {
    useAuth.getState().setTokens("acc", "ref");
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue(json(200, { ok: true }));
    await api("/agents");
    expect(f).toHaveBeenCalledWith("/api/v1/agents", expect.objectContaining({ headers: expect.objectContaining({ Authorization: "Bearer acc" }) }));
  });

  it("refreshes once on 401 then retries with the rotated token", async () => {
    useAuth.getState().setTokens("expired", "rt1");
    const f = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { error: { code: "unauthenticated", message: "expired" } }))
      .mockResolvedValueOnce(json(200, { access_token: "fresh", refresh_token: "rt2" }))
      .mockResolvedValueOnce(json(200, { items: [] }));
    const r = await api<{ items: unknown[] }>("/agents");
    expect(r.items).toEqual([]);
    expect(f).toHaveBeenCalledTimes(3);
    expect(useAuth.getState().accessToken).toBe("fresh");
    expect(useAuth.getState().refreshToken).toBe("rt2");
  });

  it("logs out when refresh fails", async () => {
    useAuth.getState().setTokens("expired", "bad");
    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(json(401, { error: { code: "unauthenticated", message: "x" } }))
      .mockResolvedValueOnce(json(401, { error: { code: "unauthenticated", message: "reuse" } }));
    await expect(api("/agents")).rejects.toBeInstanceOf(ApiError);
    expect(useAuth.getState().refreshToken).toBeNull();
  });

  it("maps problem details into readable messages", () => {
    const e = new ApiError(422, "validation_failed", "Request validation failed", { errors: [{ loc: ["body", "name"], msg: "too short" }] });
    expect(errorMessage(e)).toBe("Request validation failed: name too short");
  });
});
