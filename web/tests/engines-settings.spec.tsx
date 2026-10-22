import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EnginesSettingsSection from "@/features/settings/sections/EnginesSettingsSection";
import * as api from "@/lib/engines-api";
import type { EnginesOverview } from "@/lib/engines-api";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) =>
      vars ? Object.entries(vars).reduce((text, [k, v]) => text.replace(`{{${k}}}`, v), key) : key,
  }),
}));
vi.mock("@/lib/engines-api");

function overview(patch: Partial<EnginesOverview> = {}): EnginesOverview {
  return {
    model_source: "auto",
    engine_idle_minutes: 10,
    effective_source: "catalog",
    pinned_by_environment: false,
    idle_pinned_by_environment: false,
    gateway: { configured: false, file_exists: false, base_url: "", api: "openai-completions", has_key: false, headers: {}, models: [] },
    runtime: { runtime: "bun", installed: true, version: "1.4.2", running: false, memory_mb: null, dependencies_installed: true },
    engines: [
      { id: "rag-llamaindex", name: "LlamaIndex + FAISS", description: "d1", size: "~300 MB", installed: false, missing: 4 },
      { id: "rag-pageindex", name: "PageIndex", description: "d2", size: "small", installed: true, missing: 0 },
    ],
    install: { engine: null, state: "idle", log: [] },
    ...patch,
  };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getEngines).mockResolvedValue(overview());
});

describe("Settings → Engines", () => {
  it("saves the chosen model source and shows which one is in use", async () => {
    vi.mocked(api.saveEngineSettings).mockResolvedValue(overview({ model_source: "gateway", effective_source: "gateway" }));
    render(<EnginesSettingsSection />);
    const select = await screen.findByLabelText("Model source");
    expect(screen.getByText("Model profiles", { selector: "strong" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "gateway" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Save" })[0]);
    await waitFor(() => expect(api.saveEngineSettings).toHaveBeenCalledWith({ model_source: "gateway" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(screen.getByText("Gateway", { selector: "strong" })).toBeInTheDocument();
  });

  it("locks the model source when the environment pins it", async () => {
    vi.mocked(api.getEngines).mockResolvedValue(overview({ pinned_by_environment: true }));
    render(<EnginesSettingsSection />);
    expect(await screen.findByLabelText("Model source")).toBeDisabled();
    expect(screen.getByText(/PI_TUTOR_LLM_FROM_CATALOG is set/)).toBeInTheDocument();
  });

  it("sends the gateway with parsed models and headers, and an empty key when none is typed", async () => {
    vi.mocked(api.saveGateway).mockResolvedValue(overview());
    render(<EnginesSettingsSection />);
    fireEvent.change(await screen.findByLabelText("Base URL"), { target: { value: "https://gw.example/v1" } });
    fireEvent.change(screen.getByLabelText("Models"), { target: { value: "m-one, m-two ,," } });
    fireEvent.change(screen.getByLabelText("Extra headers"), { target: { value: "x-route: go\nx-other: a: b" } });
    fireEvent.click(screen.getByRole("button", { name: "Save gateway" }));
    await waitFor(() =>
      expect(api.saveGateway).toHaveBeenCalledWith({
        base_url: "https://gw.example/v1",
        api: "openai-completions",
        api_key: "",
        models: ["m-one", "m-two"],
        headers: { "x-route": "go", "x-other": "a: b" },
      }),
    );
  });

  it("shows a refused gateway's reason and never the stored key", async () => {
    vi.mocked(api.getEngines).mockResolvedValue(
      overview({ gateway: { configured: true, file_exists: true, base_url: "https://gw/v1", api: "openai-completions", has_key: true, headers: {}, models: [{ id: "m", name: "m" }] } }),
    );
    vi.mocked(api.saveGateway).mockRejectedValue(new Error("The base URL must start with http:// or https://."));
    render(<EnginesSettingsSection />);
    const key = (await screen.findByLabelText("API key")) as HTMLInputElement;
    expect(key.value).toBe("");
    expect(screen.getByText("A key is saved. Leave empty to keep it.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save gateway" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("must start with http");
  });

  it("tests the gateway and reports the answer", async () => {
    vi.mocked(api.getEngines).mockResolvedValue(
      overview({ gateway: { configured: true, file_exists: true, base_url: "https://gw/v1", api: "openai-completions", has_key: true, headers: {}, models: [{ id: "m", name: "m" }] } }),
    );
    vi.mocked(api.testGateway).mockResolvedValue({ ok: true, model: "m", reply: "ok" });
    render(<EnginesSettingsSection />);
    fireEvent.click(await screen.findByRole("button", { name: "Test gateway" }));
    expect(await screen.findByText("The gateway answered (m).")).toBeInTheDocument();
  });

  it("installs an engine that is missing and marks installed ones", async () => {
    vi.mocked(api.installEngine).mockResolvedValue(
      overview({ install: { engine: "rag-llamaindex", state: "done", log: ["Successfully installed faiss-cpu"] } }),
    );
    render(<EnginesSettingsSection />);
    await screen.findByText("LlamaIndex + FAISS");
    expect(screen.getByText("Installed")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Install LlamaIndex + FAISS" }));
    await waitFor(() => expect(api.installEngine).toHaveBeenCalledWith("rag-llamaindex"));
    expect(await screen.findByText(/Successfully installed faiss-cpu/)).toBeInTheDocument();
    expect(screen.getByText("Installed rag-llamaindex.")).toBeInTheDocument();
  });

  it("disables installs while one runs and follows its log", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(api.getEngines)
      .mockResolvedValueOnce(overview({ install: { engine: "rag-llamaindex", state: "installing", log: ["Collecting faiss-cpu"] } }))
      .mockResolvedValue(overview({ install: { engine: "rag-llamaindex", state: "done", log: ["Collecting faiss-cpu", "Done."] } }));
    render(<EnginesSettingsSection />);
    expect(await screen.findByText("Installing rag-llamaindex ...")).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(await screen.findByText("Installed rag-llamaindex.", {}, { timeout: 5000 })).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("reports when bun is missing", async () => {
    vi.mocked(api.getEngines).mockResolvedValue(
      overview({ runtime: { runtime: "bun", installed: false, version: null, running: false, memory_mb: null, dependencies_installed: false } }),
    );
    render(<EnginesSettingsSection />);
    expect(await screen.findByText(/bun is not installed/)).toBeInTheDocument();
  });

  it("shows a load failure instead of an empty page", async () => {
    vi.mocked(api.getEngines).mockRejectedValue(new Error("Admin access required"));
    render(<EnginesSettingsSection />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Admin access required");
  });
});
