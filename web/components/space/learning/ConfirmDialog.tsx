"use client";

import type { RefObject } from "react";
import { Button } from "@/shared/ui/Button";
import { Dialog } from "@/shared/ui/Dialog";

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = false,
  busy = false,
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
  /** Focus returns to the opener inside Dialog; kept so callers need no change. */
  returnFocusRef?: RefObject<HTMLElement | null>;
}) {
  return (
    <Dialog
      open
      alert
      size="sm"
      title={title}
      description={description}
      closeLabel={cancelLabel}
      busy={busy}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "danger" : "primary"}
            size="sm"
            onClick={onConfirm}
            loading={busy}
            data-autofocus
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {null}
    </Dialog>
  );
}
