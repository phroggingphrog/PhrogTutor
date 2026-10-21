"use client";

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/shared/ui/Button";
import { Dialog } from "@/shared/ui/Dialog";

/**
 * One text field in a dialog: a note name (`folder/name` puts it in a folder), or an instruction
 * for the AI. Replaces window.prompt, which cannot be styled, translated or reached by a screen
 * reader's dialog handling.
 */
export default function NoteNameDialog({
  open,
  title,
  label,
  initial,
  confirmLabel,
  multiline = false,
  validate,
  onSubmit,
  onClose,
}: {
  open: boolean;
  title: string;
  label: string;
  initial: string;
  confirmLabel: string;
  multiline?: boolean;
  /** Returns an error message, or "" when the value is acceptable. */
  validate?: (value: string) => string;
  onSubmit: (value: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setValue(initial);
      setError("");
    }
  }, [open, initial]);

  const submit = () => {
    const trimmed = value.trim();
    const problem = trimmed ? (validate?.(trimmed) ?? "") : t("Enter a value.");
    if (problem) return setError(problem);
    onSubmit(trimmed);
  };

  const field =
    "mt-1 w-full rounded-lg border border-[var(--border)] bg-[var(--background)] px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]";

  return (
    <Dialog
      open={open}
      title={title}
      onClose={onClose}
      size="sm"
      closeLabel={t("Close")}
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{t("Cancel")}</Button>
          <Button onClick={submit}>{confirmLabel}</Button>
        </div>
      }
    >
      <form
        className="p-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="block text-sm font-medium">
          {label}
          {multiline ? (
            <textarea
              data-autofocus
              rows={3}
              value={value}
              onChange={(event) => { setValue(event.target.value); setError(""); }}
              onKeyDown={(event) => { if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit(); }}
              aria-invalid={error ? true : undefined}
              className={field}
            />
          ) : (
            <input
              data-autofocus
              value={value}
              onChange={(event) => { setValue(event.target.value); setError(""); }}
              aria-invalid={error ? true : undefined}
              className={field}
            />
          )}
        </label>
        {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
      </form>
    </Dialog>
  );
}
