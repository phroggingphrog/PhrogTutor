"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  SettingRow,
  SettingSection,
  SettingsPageHeader,
  inputClass,
  selectClass,
} from "@/components/settings/shared";
import Button from "@/components/ui/Button";
import {
  getEngines,
  installEngine,
  restartEngine,
  saveEngineSettings,
  saveGateway,
  testGateway,
  type EnginesOverview,
  type GatewayApi,
  type ModelSource,
} from "@/lib/engines-api";

const SOURCES: { value: ModelSource; label: string; hint: string }[] = [
  {
    value: "auto",
    label: "Automatic",
    hint: "Use the model profile chosen in Settings, unless a gateway is configured below.",
  },
  {
    value: "catalog",
    label: "Model profiles",
    hint: "Always use the provider, model and key chosen under Models. The gateway is ignored.",
  },
  {
    value: "gateway",
    label: "Gateway",
    hint: "Always send requests to the gateway below, whatever profile is selected.",
  },
];

const APIS: { value: GatewayApi; label: string }[] = [
  { value: "openai-completions", label: "OpenAI-compatible (chat completions)" },
  { value: "openai-responses", label: "OpenAI Responses" },
  { value: "anthropic-messages", label: "Anthropic Messages" },
];

function Notice({ tone, children }: { tone: "ok" | "error" | "info"; children: React.ReactNode }) {
  const style =
    tone === "ok"
      ? "border-success/30 bg-success/10 text-success"
      : tone === "error"
        ? "border-destructive/30 bg-destructive/10 text-destructive"
        : "border-[var(--border)] bg-[var(--muted)]/30 text-[var(--muted-foreground)]";
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`mt-3 rounded-lg border px-3 py-2 text-[12px] ${style}`}>
      {children}
    </div>
  );
}

export default function EnginesSettingsSection() {
  const { t } = useTranslation();
  const [data, setData] = useState<EnginesOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; text: string; where: string } | null>(null);
  const [source, setSource] = useState<ModelSource>("auto");
  const [idleMinutes, setIdleMinutes] = useState(10);
  const [gateway, setGateway] = useState({ base_url: "", api: "openai-completions" as GatewayApi, api_key: "", models: "", headers: "" });
  const logRef = useRef<HTMLPreElement | null>(null);

  const adopt = useCallback((next: EnginesOverview, resetForms = true) => {
    setData(next);
    if (resetForms) {
      setSource(next.model_source);
      setIdleMinutes(next.engine_idle_minutes);
      setGateway({
        base_url: next.gateway.base_url,
        api: next.gateway.api,
        api_key: "",
        models: next.gateway.models.map((m) => m.id).join(", "),
        headers: Object.entries(next.gateway.headers).map(([k, v]) => `${k}: ${v}`).join("\n"),
      });
    }
  }, []);

  useEffect(() => {
    getEngines().then((next) => adopt(next)).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [adopt]);

  // An install runs in the background on the server: follow it until it settles.
  const installing = data?.install.state === "installing";
  useEffect(() => {
    if (!installing) return;
    const timer = window.setInterval(() => {
      getEngines().then((next) => adopt(next, false)).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [installing, adopt]);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [data?.install.log.length]);

  const run = async (key: string, action: () => Promise<EnginesOverview | void>, success: string) => {
    setBusy(key);
    setNotice(null);
    try {
      const next = await action();
      if (next) adopt(next, key !== "install");
      setNotice({ tone: "ok", text: success, where: key });
    } catch (err) {
      setNotice({ tone: "error", text: err instanceof Error ? err.message : String(err), where: key });
    } finally {
      setBusy(null);
    }
  };

  const parseHeaders = () =>
    Object.fromEntries(
      gateway.headers
        .split("\n")
        .map((line) => line.split(/:(.*)/s))
        .filter((parts) => parts[0]?.trim())
        .map((parts) => [parts[0].trim(), (parts[1] ?? "").trim()]),
    );
  const parseModels = () => gateway.models.split(/[,\n]+/).map((m) => m.trim()).filter(Boolean);

  const message = (where: string) =>
    notice && notice.where === where ? <Notice tone={notice.tone}>{notice.text}</Notice> : null;

  if (error) return <Notice tone="error">{error}</Notice>;
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-[13px] text-[var(--muted-foreground)]">
        <Loader2 className="h-4 w-4 animate-spin" /> {t("Loading engine settings...")}
      </div>
    );
  }

  const activeHint = SOURCES.find((s) => s.value === source)?.hint ?? "";
  return (
    <div>
      <SettingsPageHeader
        title={t("Engines")}
        description={t("Choose where model requests go, run the pi engine, and install optional engines.")}
      />

      <SettingSection
        title={t("Model source")}
        description={t("Which configuration answers your chats and tools.")}
      >
        <SettingRow
          title={t("Use")}
          description={t(activeHint)}
          control={
            <select
              aria-label={t("Model source")}
              className={`${selectClass} min-w-[200px] pr-8`}
              value={source}
              disabled={data.pinned_by_environment}
              onChange={(e) => setSource(e.target.value as ModelSource)}
            >
              {SOURCES.map((s) => (
                <option key={s.value} value={s.value}>
                  {t(s.label)}
                </option>
              ))}
            </select>
          }
        />
        <div className="flex flex-wrap items-center gap-3 pb-2 text-[12px] text-[var(--muted-foreground)]">
          <span>
            {t("In use now")}: <strong>{data.effective_source === "gateway" ? t("Gateway") : t("Model profiles")}</strong>
          </span>
          <Button
            size="sm"
            loading={busy === "source"}
            disabled={data.pinned_by_environment || source === data.model_source}
            onClick={() => run("source", () => saveEngineSettings({ model_source: source }), t("Saved."))}
          >
            {t("Save")}
          </Button>
        </div>
        {data.pinned_by_environment && (
          <Notice tone="info">{t("PI_TUTOR_LLM_FROM_CATALOG is set in the environment, which overrides this choice.")}</Notice>
        )}
        {message("source")}
      </SettingSection>

      <SettingSection
        title={t("Gateway")}
        description={t("One endpoint for every request when the source is Gateway. Models you list here are the ones offered.")}
      >
        <SettingRow
          title={t("Base URL")}
          control={
            <input
              aria-label={t("Base URL")}
              className={`${inputClass} w-[320px] max-w-full`}
              placeholder={t("https://gateway.example/v1")}
              value={gateway.base_url}
              onChange={(e) => setGateway({ ...gateway, base_url: e.target.value })}
            />
          }
        />
        <SettingRow
          title={t("API format")}
          control={
            <select
              aria-label={t("API format")}
              className={`${selectClass} min-w-[260px] pr-8`}
              value={gateway.api}
              onChange={(e) => setGateway({ ...gateway, api: e.target.value as GatewayApi })}
            >
              {APIS.map((a) => (
                <option key={a.value} value={a.value}>
                  {t(a.label)}
                </option>
              ))}
            </select>
          }
        />
        <SettingRow
          title={t("API key")}
          description={data.gateway.has_key ? t("A key is saved. Leave empty to keep it.") : t("Not set.")}
          control={
            <input
              aria-label={t("API key")}
              type="password"
              autoComplete="off"
              className={`${inputClass} w-[320px] max-w-full`}
              placeholder={data.gateway.has_key ? "••••••••" : ""}
              value={gateway.api_key}
              onChange={(e) => setGateway({ ...gateway, api_key: e.target.value })}
            />
          }
        />
        <SettingRow
          title={t("Models")}
          description={t("Model ids, separated by commas.")}
          control={
            <input
              aria-label={t("Models")}
              className={`${inputClass} w-[320px] max-w-full`}
              placeholder={t("model-a, model-b")}
              value={gateway.models}
              onChange={(e) => setGateway({ ...gateway, models: e.target.value })}
            />
          }
        />
        <SettingRow
          title={t("Extra headers")}
          description={t("One per line, as Name: value. Optional.")}
          control={
            <textarea
              aria-label={t("Extra headers")}
              rows={3}
              className={`${inputClass} w-[320px] max-w-full font-mono`}
              value={gateway.headers}
              onChange={(e) => setGateway({ ...gateway, headers: e.target.value })}
            />
          }
        />
        <div className="flex flex-wrap items-center gap-2 pb-2">
          <Button
            size="sm"
            loading={busy === "gateway"}
            onClick={() =>
              run(
                "gateway",
                () => saveGateway({ ...gateway, models: parseModels(), headers: parseHeaders() }),
                t("Gateway saved. The engine restarts with it."),
              )
            }
          >
            {t("Save gateway")}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            loading={busy === "test"}
            disabled={!data.gateway.configured}
            onClick={async () => {
              setBusy("test");
              setNotice(null);
              try {
                const result = await testGateway();
                setNotice(
                  result.ok
                    ? { tone: "ok", where: "gateway", text: t("The gateway answered ({{model}}).", { model: result.model }) }
                    : { tone: "error", where: "gateway", text: result.error || t("The gateway did not answer.") },
                );
              } catch (err) {
                setNotice({ tone: "error", where: "gateway", text: err instanceof Error ? err.message : String(err) });
              } finally {
                setBusy(null);
              }
            }}
          >
            {t("Test gateway")}
          </Button>
        </div>
        {message("gateway")}
      </SettingSection>

      <SettingSection
        title={t("pi engine")}
        description={t("The runtime that talks to your models. It starts on the first request and stops when idle.")}
      >
        <SettingRow
          title={t("Runtime")}
          description={
            data.runtime.installed
              ? t("bun {{version}} is installed.", { version: data.runtime.version ?? "" })
              : t("bun is not installed. Install it from bun.sh, then restart the app.")
          }
          control={
            <span className="inline-flex items-center gap-1.5 text-[13px]">
              {data.runtime.installed && data.runtime.dependencies_installed ? (
                <CheckCircle2 className="h-4 w-4 text-success" aria-hidden />
              ) : (
                <XCircle className="h-4 w-4 text-destructive" aria-hidden />
              )}
              {data.runtime.running
                ? t("Running{{memory}}", { memory: data.runtime.memory_mb ? ` · ${Math.round(data.runtime.memory_mb)} MB` : "" })
                : t("Idle")}
            </span>
          }
        />
        <SettingRow
          title={t("Stop when idle after")}
          description={t("Minutes without a request before the engine exits and frees its memory. 0 keeps it running.")}
          control={
            <div className="flex items-center gap-2">
              <input
                aria-label={t("Stop when idle after")}
                type="number"
                min={0}
                max={1440}
                className={`${inputClass} w-24`}
                disabled={data.idle_pinned_by_environment}
                value={idleMinutes}
                onChange={(e) => setIdleMinutes(Number(e.target.value))}
              />
              <Button
                size="sm"
                loading={busy === "idle"}
                disabled={data.idle_pinned_by_environment || idleMinutes === data.engine_idle_minutes}
                onClick={() => run("idle", () => saveEngineSettings({ engine_idle_minutes: idleMinutes }), t("Saved."))}
              >
                {t("Save")}
              </Button>
            </div>
          }
        />
        <div className="pb-2">
          <Button
            size="sm"
            variant="secondary"
            loading={busy === "restart"}
            disabled={!data.runtime.running}
            onClick={() => run("restart", () => restartEngine(), t("Engine stopped. It starts again on the next request."))}
          >
            {t("Restart engine")}
          </Button>
        </div>
        {message("idle")}
        {message("restart")}
      </SettingSection>

      <SettingSection
        title={t("Optional engines")}
        description={t("Extra capabilities that are not installed by default. Installing runs pip on the server and can take a few minutes.")}
      >
        {data.engines.map((engine) => (
          <SettingRow
            key={engine.id}
            title={engine.name}
            description={`${t(engine.description)} (${engine.size})`}
            control={
              engine.installed ? (
                <span className="inline-flex items-center gap-1.5 text-[13px] text-success">
                  <CheckCircle2 className="h-4 w-4" aria-hidden /> {t("Installed")}
                </span>
              ) : (
                <Button
                  size="sm"
                  aria-label={`${t("Install")} ${engine.name}`}
                  loading={busy === "install" || (installing && data.install.engine === engine.id)}
                  disabled={installing}
                  onClick={() => run("install", () => installEngine(engine.id), t("Install started."))}
                >
                  {t("Install")}
                </Button>
              )
            }
          />
        ))}
        {data.install.state !== "idle" && (
          <div className="mt-3">
            <div className="mb-1 text-[12px] font-medium text-[var(--foreground)]" role="status">
              {data.install.state === "installing" && t("Installing {{name}} ...", { name: data.install.engine })}
              {data.install.state === "done" && t("Installed {{name}}.", { name: data.install.engine })}
              {data.install.state === "failed" && t("The install of {{name}} failed.", { name: data.install.engine })}
            </div>
            <pre
              ref={logRef}
              className="max-h-48 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--muted)]/30 p-3 text-[11px] leading-relaxed"
            >
              {data.install.log.join("\n")}
            </pre>
          </div>
        )}
        {message("install")}
      </SettingSection>
    </div>
  );
}
