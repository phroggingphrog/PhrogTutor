"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch, apiUrl } from "@/lib/api";

// Drop raw course material (PDF, slides, books, websites) into a knowledge base the tutor can use.
// Uses DeepTutor's own knowledge-base upload and web-source crawl; nothing new is stored here.
export default function CourseMaterialDrop() {
  const [course, setCourse] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [url, setUrl] = useState("");
  const [consent, setConsent] = useState(false);
  const [local, setLocal] = useState(false);
  const [localModel, setLocalModel] = useState("");
  const [status, setStatus] = useState("");
  const [done, setDone] = useState("");

  useEffect(() => {
    apiFetch(apiUrl("/api/mastery-records/course-material-options"))
      .then(async (r) => r.ok && setLocalModel((await r.json()).local_model))
      .catch(() => {});
  }, []);

  const name = course.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const ready = consent && !!name && (!!files?.length || !!url.trim());

  const submit = async () => {
    setStatus("Uploading…");
    setDone("");
    const form = new FormData();
    for (const f of Array.from(files ?? [])) form.append("files", f);
    let r: Response | null = null;
    if (files?.length) r = await apiFetch(apiUrl(`/api/knowledge-bases/${encodeURIComponent(name)}/upload`), { method: "POST", body: form });
    if (!r || r.status === 404) {
      const create = new FormData();
      create.append("name", name);
      for (const f of Array.from(files ?? [])) create.append("files", f);
      if (local && localModel) create.append("indexing_llm", localModel);
      r = await apiFetch(apiUrl("/api/knowledge-bases"), { method: "POST", body: create });
    }
    if (!r.ok) return setStatus(`Upload failed (${r.status})`);
    if (url.trim()) {
      const w = await apiFetch(apiUrl(`/api/knowledge-bases/${encodeURIComponent(name)}/web-source`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim(), max_depth: 1, max_pages: 30 }),
      });
      if (!w.ok) return setStatus(`Files added, website failed (${w.status})`);
    }
    setStatus("");
    setDone(name);
    setFiles(null);
    setUrl("");
  };

  return (
    <section className="mb-9 rounded-xl border border-[var(--border)] p-4">
      <h2 className="mb-1 text-[13.5px] font-semibold">Add course material</h2>
      <p className="mb-3 text-xs text-[var(--muted-foreground)]">
        PDFs, slides, books or a website. It is parsed into a knowledge base the tutor can search.
      </p>
      <div className="grid gap-2 text-sm">
        <input value={course} onChange={(e) => setCourse(e.target.value)} placeholder="Course or topic name (e.g. Statistics 101)"
          className="rounded border border-[var(--border)] bg-transparent px-2 py-1.5 outline-none" />
        <input type="file" multiple accept=".pdf,.ppt,.pptx,.doc,.docx,.epub,.md,.txt,.html" onChange={(e) => setFiles(e.target.files)} />
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="…or a website URL (optional)"
          className="rounded border border-[var(--border)] bg-transparent px-2 py-1.5 outline-none" />
        <label className="flex items-start gap-2 text-xs">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} className="mt-0.5" />
          <span>I own this material or have the right to use it, and I agree to it being processed by AI models to help me study.</span>
        </label>
        <label className={`flex items-start gap-2 text-xs ${localModel ? "" : "opacity-50"}`}>
          <input type="checkbox" disabled={!localModel} checked={local} onChange={(e) => setLocal(e.target.checked)} className="mt-0.5" />
          <span>
            Process with a local model only{localModel ? ` (${localModel})` : " — not set up (PI_TUTOR_LOCAL_MODEL)"}. Only applies when the course is new.
          </span>
        </label>
        <div className="flex items-center gap-3">
          <button disabled={!ready || !!status} onClick={submit}
            className="rounded bg-[var(--primary)] px-3 py-1.5 text-sm font-medium text-[var(--primary-foreground)] disabled:opacity-50">
            Add material
          </button>
          <span className="text-xs text-[var(--muted-foreground)]">{status}</span>
          {done && <Link href="/knowledge-bases" className="text-xs hover:underline">Added to “{done}” — processing in the background →</Link>}
        </div>
      </div>
    </section>
  );
}
