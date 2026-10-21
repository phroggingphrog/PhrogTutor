import { apiFetch, apiUrl } from "@/lib/api";

export interface PiPackage {
  source: string;
  /** The entry narrows which of the package's resources load. */
  filtered: boolean;
}

async function call(path: string, init?: RequestInit): Promise<PiPackage[]> {
  const response = await apiFetch(apiUrl(`/api/settings/pi-packages${path}`), init);
  const body = (await response.json().catch(() => ({}))) as {
    packages?: PiPackage[];
    detail?: string;
  };
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body.packages ?? [];
}

export const listPiPackages = () => call("");

export const installPiPackage = (source: string) =>
  call("", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });

export const removePiPackage = (source: string) =>
  call("/remove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source }),
  });
