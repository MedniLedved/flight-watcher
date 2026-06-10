import { useEffect, useState } from "react";
import type { AgentConfig, LatestFile, StatsFile } from "@/types/data";

const LATEST_URL = "data/latest.json";
const STATS_URL = "data/stats.json";
const AGENT_CONFIG_URL = "config/agent.json";

export interface DataLoaderState {
  latest: LatestFile | null;
  stats: StatsFile | null;
  agentConfig: AgentConfig | null;
  loading: boolean;
  error: string | null;
}

export async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${import.meta.env.BASE_URL}${url}`);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

/** Načte statické JSONy pro dashboard. Frontend je „hloupý" – jen čte
 *  hotová data spočítaná scannerem v CI, nic nepřepočítává. */
export function useDataLoader(): DataLoaderState {
  const [state, setState] = useState<DataLoaderState>({
    latest: null,
    stats: null,
    agentConfig: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [latest, stats, agentConfig] = await Promise.all([
          fetchJson<LatestFile>(LATEST_URL),
          fetchJson<StatsFile>(STATS_URL),
          fetchJson<AgentConfig>(AGENT_CONFIG_URL),
        ]);
        if (!cancelled) {
          setState({ latest, stats, agentConfig, loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            latest: null, stats: null, agentConfig: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return state;
}
