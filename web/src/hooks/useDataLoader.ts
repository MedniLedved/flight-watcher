import { useEffect, useState } from "react";
import type { AgentConfig, LatestFile } from "@/types/data";

/** Soubory načítané při startu (Fáze 1: latest.json + config/agent.json).
 *  Cesty jsou relativní k base, aby fungoval deploy do podsložky (Pages). */
const LATEST_URL = "data/latest.json";
const AGENT_CONFIG_URL = "config/agent.json";

export interface DataLoaderState {
  latest: LatestFile | null;
  agentConfig: AgentConfig | null;
  loading: boolean;
  error: string | null;
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${import.meta.env.BASE_URL}${url}`);
  if (!res.ok) {
    throw new Error(`${url}: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

/** Načte statické JSONy pro dashboard. Frontend je „hloupý" – jen čte
 *  hotová data spočítaná scannerem v CI, nic nepřepočítává. */
export function useDataLoader(): DataLoaderState {
  const [state, setState] = useState<DataLoaderState>({
    latest: null,
    agentConfig: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [latest, agentConfig] = await Promise.all([
          fetchJson<LatestFile>(LATEST_URL),
          fetchJson<AgentConfig>(AGENT_CONFIG_URL),
        ]);
        if (!cancelled) {
          setState({ latest, agentConfig, loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            latest: null,
            agentConfig: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
