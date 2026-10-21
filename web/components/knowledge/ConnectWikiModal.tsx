"use client";

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen, Loader2 } from "lucide-react";
import Modal from "@/components/common/Modal";
import { probeWiki } from "@/features/knowledge/api/catalog";

interface Props {
  onClose: () => void;
  onConnect: (params: { name: string; serverUrl: string; collections: string[] }) => Promise<void>;
}

export default function ConnectWikiModal({ onClose, onConnect }: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [collections, setCollections] = useState("");
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState<"probe" | "connect" | null>(null);
  const [error, setError] = useState("");

  const inputClass = "mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-[13px] text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]";
  const collectionList = () => collections.split(",").map((part) => part.trim()).filter(Boolean);
  const probe = async () => {
    setBusy("probe");
    setError("");
    try {
      await probeWiki({ serverUrl: serverUrl.trim() });
      setVerified(true);
    } catch (cause) {
      setVerified(false);
      setError(cause instanceof Error ? cause.message : t("Could not reach the wiki"));
    } finally {
      setBusy(null);
    }
  };
  const connect = async () => {
    setBusy("connect");
    setError("");
    try {
      await onConnect({ name: name.trim(), serverUrl: serverUrl.trim(), collections: collectionList() });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("Could not connect the wiki"));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal isOpen onClose={onClose} title={t("Connect LLM wiki")} titleIcon={<BookOpen className="h-4 w-4" />} footer={
      <div className="flex justify-end gap-2">
        <button type="button" onClick={onClose} disabled={!!busy} className="rounded-lg px-3 py-2 text-[13px] text-[var(--muted-foreground)]">{t("Cancel")}</button>
        <button type="button" onClick={() => void connect()} disabled={!!busy || !name.trim()} className="rounded-lg bg-[var(--primary)] px-3 py-2 text-[13px] font-medium text-[var(--primary-foreground)] disabled:opacity-50">
          {busy === "connect" && <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />}{t("Connect wiki")}
        </button>
      </div>
    }>
      <div className="space-y-4 p-4">
        <p className="text-[12px] text-[var(--muted-foreground)]">{t("Searches your markdown wiki through its qmd server. qmd keeps the index and embeddings; DeepTutor reads matching notes and never copies or changes them.")}</p>
        <label className="block text-[12px] font-medium">{t("Knowledge base name")}
          <input value={name} onChange={(event) => { setName(event.target.value); setError(""); }} className={inputClass} placeholder={t("my-wiki")} data-autofocus />
        </label>
        <label className="block text-[12px] font-medium">{t("qmd server URL")}
          <input value={serverUrl} onChange={(event) => { setServerUrl(event.target.value); setVerified(false); setError(""); }} className={inputClass} placeholder={t("http://127.0.0.1:8906/mcp")} />
          <span className="mt-1 block text-[11px] font-normal text-[var(--muted-foreground)]">{t("Leave empty to use the default local address.")}</span>
        </label>
        <label className="block text-[12px] font-medium">{t("Collections")}
          <input value={collections} onChange={(event) => setCollections(event.target.value)} className={inputClass} placeholder={t("university, personal-learning")} />
          <span className="mt-1 block text-[11px] font-normal text-[var(--muted-foreground)]">{t("Comma-separated qmd collections. Leave empty to search the defaults.")}</span>
        </label>
        <div className="flex items-center gap-3">
          <button type="button" onClick={() => void probe()} disabled={!!busy} className="rounded-lg border border-[var(--border)] px-3 py-2 text-[12px] font-medium disabled:opacity-50">
            {busy === "probe" && <Loader2 className="mr-1 inline h-3.5 w-3.5 animate-spin" />}{t("Check connection")}
          </button>
          {verified && <span className="text-[12px] text-success">{t("Ready")}</span>}
        </div>
        {error && <p role="alert" className="text-[12px] text-destructive">{error}</p>}
      </div>
    </Modal>
  );
}
