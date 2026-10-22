import { apiFetch, apiUrl } from "@/lib/api";

export type ModelSource = "auto" | "catalog" | "gateway";
export type GatewayApi = "openai-completions" | "openai-responses" | "anthropic-messages";

export interface Gateway {
  configured: boolean;
  file_exists: boolean;
  base_url: string;
  api: GatewayApi;
  has_key: boolean;
  headers: Record<string, string>;
  models: { id: string; name: string }[];
}

export interface OptionalEngine {
  id: string;
  name: string;
  description: string;
  size: string;
  installed: boolean;
  missing: number;
}

export interface InstallJob {
  engine: string | null;
  state: "idle" | "installing" | "done" | "failed";
  log: string[];
}

export interface EnginesOverview {
  model_source: ModelSource;
  engine_idle_minutes: number;
  effective_source: "catalog" | "gateway";
  pinned_by_environment: boolean;
  idle_pinned_by_environment: boolean;
  gateway: Gateway;
  runtime: {
    runtime: string;
    installed: boolean;
    version: string | null;
    running: boolean;
    memory_mb: number | null;
    dependencies_installed: boolean;
  };
  engines: OptionalEngine[];
  install: InstallJob;
}

export interface GatewayInput {
  base_url: string;
  api: GatewayApi;
  /** Empty keeps the stored key. */
  api_key: string;
  headers: Record<string, string>;
  models: string[];
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(apiUrl(`/api/settings/engines${path}`), {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  const body = (await response.json().catch(() => ({}))) as T & { detail?: string };
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

const json = (method: string, data?: unknown): RequestInit => ({
  method,
  body: data === undefined ? undefined : JSON.stringify(data),
});

export const getEngines = () => call<EnginesOverview>("");
export const saveEngineSettings = (data: { model_source?: ModelSource; engine_idle_minutes?: number }) =>
  call<EnginesOverview>("/settings", json("PUT", data));
export const saveGateway = (data: GatewayInput) => call<EnginesOverview>("/gateway", json("PUT", data));
export const testGateway = () =>
  call<{ ok: boolean; model?: string; reply?: string; error?: string }>("/gateway/test", json("POST"));
export const restartEngine = () => call<EnginesOverview>("/restart", json("POST"));
export const installEngine = (engine: string) => call<EnginesOverview>("/install", json("POST", { engine }));
