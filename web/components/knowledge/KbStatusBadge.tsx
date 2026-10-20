"use client";

import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import {
  kbHasLiveProgress,
  kbNeedsReindex,
  kbEmbeddingUnavailable,
  resolveKbStatus,
  type KnowledgeBase,
} from "@/lib/knowledge-helpers";

interface KbStatusBadgeProps {
  kb: KnowledgeBase;
  isReindexingLocally?: boolean;
}

export default function KbStatusBadge({
  kb,
  isReindexingLocally = false,
}: KbStatusBadgeProps) {
  const { t } = useTranslation();
  const status = resolveKbStatus(kb);
  const needsReindex = kbNeedsReindex(kb);
  const isLive = kbHasLiveProgress(kb) || isReindexingLocally;
  const isError = status === "error";
  const embeddingUnavailable = kbEmbeddingUnavailable(kb);
  const isReady = status === "ready" && !needsReindex && !embeddingUnavailable;

  const tone =
    needsReindex || embeddingUnavailable
      ? "bg-warning-surface text-warning"
      : isError
        ? "bg-destructive/10 text-destructive"
        : isLive
          ? "bg-sky-100 text-sky-700 dark:bg-sky-950/30 dark:text-sky-300"
          : "bg-success-surface text-success";

  const Icon = isLive ? Clock3 : isReady ? CheckCircle2 : AlertTriangle;

  const label =
    !isLive && embeddingUnavailable
      ? kb.metadata?.embedding_status === "missing"
        ? t("Embedding model deleted")
        : t("Check embedding model")
      : needsReindex
        ? t("Needs reindex")
        : isError
          ? t("Error")
          : isLive
            ? t("Processing live")
            : isReady
              ? t("Ready")
              : status.replaceAll("_", " ");

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${tone}`}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
