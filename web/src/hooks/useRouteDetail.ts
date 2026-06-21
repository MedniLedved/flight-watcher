import { useEffect, useState } from "react";
import type {
  AlternativesFile, CalendarFile, HistoryFile,
} from "@/types/data";
import { fetchJson } from "./useDataLoader";

export interface RouteDetailState {
  history: HistoryFile | null;
  calendar: CalendarFile | null;
  alternatives: AlternativesFile;
  loading: boolean;
  error: string | null;
}

/** Lazy-loaduje history/{routeKey}.json, calendar/{routeKey}.json a
 *  alternatives/{routeKey}.json. Spouští se jen když routeKey není null. */
export function useRouteDetail(routeKey: string | null): RouteDetailState {
  const [state, setState] = useState<RouteDetailState>({
    history: null, calendar: null, alternatives: [], loading: false, error: null,
  });

  useEffect(() => {
    if (!routeKey) {
      setState({ history: null, calendar: null, alternatives: [],
        loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ history: null, calendar: null, alternatives: [],
      loading: true, error: null });
    (async () => {
      try {
        const [history, calendar, alternatives] = await Promise.all([
          fetchJson<HistoryFile>(`data/history/${routeKey}.json`),
          fetchJson<CalendarFile>(`data/calendar/${routeKey}.json`).catch(() => [] as CalendarFile),
          fetchJson<AlternativesFile>(`data/alternatives/${routeKey}.json`).catch(() => [] as AlternativesFile),
        ]);
        if (!cancelled) setState({ history, calendar, alternatives,
          loading: false, error: null });
      } catch (err) {
        if (!cancelled) setState({
          history: null, calendar: null, alternatives: [], loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => { cancelled = true; };
  }, [routeKey]);

  return state;
}
