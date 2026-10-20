"use client";

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/Button";
import { Dialog } from "@/shared/ui/Dialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  /** Body content — plain text or richer markup (e.g. an avatar row). */
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** "danger" renders a red confirm button for destructive actions. */
  tone?: "default" | "danger";
  /** Disables the buttons and swaps the confirm label while pending. */
  busy?: boolean;
  busyLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * Small confirmation modal in the app's dialog style (overlay + card),
 * replacing bare window.confirm() prompts. Closes on Escape and on
 * overlay click; the cancel button takes initial focus so a stray Enter
 * never triggers a destructive action.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel,
  tone = "default",
  busy = false,
  busyLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation();
  const resolvedCancelLabel = cancelLabel ?? t("Cancel");

  return (
    <Dialog
      open={open}
      title={title}
      onClose={onCancel}
      size="sm"
      alert
      busy={busy}
      closeLabel={t("Close")}
      footer={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={onCancel}
            disabled={busy}
            data-autofocus
          >
            {resolvedCancelLabel}
          </Button>
          <Button
            variant={tone === "danger" ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? (busyLabel ?? confirmLabel) : confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}
