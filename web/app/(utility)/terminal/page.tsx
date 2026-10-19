"use client";

import dynamic from "next/dynamic";

const TerminalPanel = dynamic(() => import("@/components/notes/TerminalPanel"), { ssr: false });

// A full-page sandboxed shell (same sandbox as Notes: no network, files kept in its own home folder).
export default function TerminalPage() {
  return (
    <div className="flex h-full flex-col">
      <TerminalPanel note="_scratch" className="min-h-0 flex-1 bg-[#1e1e1e] p-1" />
    </div>
  );
}
