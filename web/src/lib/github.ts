/**
 * Minimální klient GitHub REST API pro záložku Nastavení (fáze 8 / sekce 5.8).
 *
 * Bezpečnostní model: JEDEN uživatel, JEDEN privátní repo. Fine-grained
 * Personal Access Token (Contents: read/write, volitelně Actions: read/write
 * pro „Spustit scan teď") žije pouze v `localStorage` prohlížeče tohoto
 * uživatele. Žádný backend, žádný server-side secret. Není to vhodné pro
 * veřejné/multi-user nasazení.
 */

export const GITHUB_OWNER = "medniledved";
export const GITHUB_REPO = "flight-watcher";
/** Kanonický config, který scanner čte při běhu (sekce 3). */
export const AGENT_CONFIG_PATH = "config/agent.json";
/** Větev, na kterou se commituje config a na které se spouští scan. */
export const TARGET_BRANCH = "main";
/** Workflow pro „Spustit scan teď" (workflow_dispatch). */
export const SCAN_WORKFLOW = "scan.yml";

const TOKEN_STORAGE_KEY = "flight-watcher:github-token";
const API_BASE = "https://api.github.com";

// ---------------------------------------------------------------------------
// Token v localStorage
// ---------------------------------------------------------------------------
export function loadToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* localStorage nedostupné (private mode) – token zůstane jen v paměti */
  }
}

// ---------------------------------------------------------------------------
// UTF-8 ⇄ base64 (GitHub Contents API kóduje obsah base64; config obsahuje
// české znaky, takže nestačí holé btoa/atob).
// ---------------------------------------------------------------------------
export function utf8ToBase64(str: string): string {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function base64ToUtf8(b64: string): string {
  const bin = atob(b64.replace(/\s/g, ""));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

// ---------------------------------------------------------------------------
// Nízkoúrovňové volání API
// ---------------------------------------------------------------------------
async function gh<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = "";
    try {
      const body = (await res.json()) as { message?: string };
      detail = body.message ? ` – ${body.message}` : "";
    } catch {
      /* ignore */
    }
    throw new Error(`GitHub API ${res.status}${detail}`);
  }
  // 204 No Content (workflow dispatch) nemá tělo.
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
// Contents API
// ---------------------------------------------------------------------------
interface ContentsResponse {
  sha: string;
  content: string;
  encoding: string;
}

export interface RemoteFile {
  /** dekódovaný UTF-8 obsah */
  text: string;
  /** blob SHA – nutné pro update */
  sha: string;
}

/** Načte aktuální config z repa (kvůli živému SHA i obsahu). */
export async function fetchAgentConfigFile(
  token: string,
  branch: string = TARGET_BRANCH,
): Promise<RemoteFile> {
  const data = await gh<ContentsResponse>(
    token,
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${AGENT_CONFIG_PATH}?ref=${branch}`,
  );
  return { text: base64ToUtf8(data.content), sha: data.sha };
}

/** Commitne nový obsah configu na danou větev. Vyžaduje aktuální `sha`. */
export async function commitAgentConfig(
  token: string,
  contentText: string,
  sha: string,
  message: string,
  branch: string = TARGET_BRANCH,
): Promise<string> {
  const data = await gh<{ commit: { sha: string } }>(
    token,
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/contents/${AGENT_CONFIG_PATH}`,
    {
      method: "PUT",
      body: JSON.stringify({
        message,
        content: utf8ToBase64(contentText),
        sha,
        branch,
      }),
    },
  );
  return data.commit.sha;
}

/** Spustí scan ručně přes workflow_dispatch (vyžaduje Actions: write). */
export async function triggerScan(token: string): Promise<void> {
  await gh<void>(
    token,
    `/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${SCAN_WORKFLOW}/dispatches`,
    { method: "POST", body: JSON.stringify({ ref: TARGET_BRANCH }) },
  );
}
