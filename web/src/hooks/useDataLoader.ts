import { useEffect, useState } from "react";
import type {
  AgentConfig,
  InsightsFile,
  LatestFile,
  RoutesFile,
  StatsFile,
} from "@/types/data";

const LATEST_URL = "data/latest.json";
const STATS_URL = "data/stats.json";
const AGENT_CONFIG_URL = "config/agent.json";
const ROUTES_URL = "data/routes.json";
const INSIGHTS_URL = "data/insights.json";

export interface DataLoaderState {
  latest: LatestFile | null;
  stats: StatsFile | null;
  agentConfig: AgentConfig | null;
  routes: RoutesFile | null;
  insights: InsightsFile | null;
  loading: boolean;
  error: string | null;
}

export async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(`${import.meta.env.BASE_URL}${url}`);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return (await res.json()) as T;
}

async function fetchJsonOptional<T>(url: string): Promise<T | null> {
  try {
    return await fetchJson<T>(url);
  } catch {
    return null;
  }
}

export function useDataLoader(): DataLoaderState {
  const [state, setState] = useState<DataLoaderState>({
    latest: null,
    stats: null,
    agentConfig: null,
    routes: null,
    insights: null,
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [latest, stats, agentConfig, routes, insights] = await Promise.all([
          fetchJson<LatestFile>(LATEST_URL),
          fetchJson<StatsFile>(STATS_URL),
          fetchJson<AgentConfig>(AGENT_CONFIG_URL),
          fetchJsonOptional<RoutesFile>(ROUTES_URL),
          fetchJsonOptional<InsightsFile>(INSIGHTS_URL),
        ]);
        if (!cancelled) {
          setState({ latest, stats, agentConfig, routes, insights, loading: false, error: null });
        }
      } catch (err) {
        if (!cancelled) {
          setState({
            latest: null, stats: null, agentConfig: null, routes: null, insights: null,
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
