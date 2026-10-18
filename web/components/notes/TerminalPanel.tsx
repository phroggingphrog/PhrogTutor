"use client";

import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";

import { wsUrl } from "@/lib/api";

// A bash shell in the server's code sandbox (no network). Files in its home folder are kept.
export default function TerminalPanel() {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const term = new Terminal({ fontSize: 13, cursorBlink: true, theme: { background: "#1e1e1e" } });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host.current);
    fit.fit();
    const ws = new WebSocket(wsUrl("/ws/terminal"));
    ws.binaryType = "arraybuffer";
    const resize = () => {
      fit.fit();
      if (ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "resize", rows: term.rows, cols: term.cols }));
    };
    ws.onopen = resize;
    ws.onmessage = (e) => term.write(new Uint8Array(e.data as ArrayBuffer));
    ws.onclose = () => term.write("\r\n[terminal closed — reopen the panel to start a new one]\r\n");
    const input = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "input", data }));
    });
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    return () => {
      observer.disconnect();
      input.dispose();
      ws.close();
      term.dispose();
    };
  }, []);

  return <div ref={host} className="h-64 shrink-0 border-t border-[var(--border)] bg-[#1e1e1e] p-1" />;
}
