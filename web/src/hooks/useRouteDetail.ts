import { useEffect, useState } from "react";
import type { CalendarFile, HistoryFile } from "@/types/data";
import { fetchJson } from "./useDataLoader";

export interface RouteDetailState {
  history: HistoryFile | null;
  calendar: CalendarFile | null;
  loading: boolean;
  error: string | null;
}

/** Lazy-loaduje history/{routeKey}.json a calendar/{routeKey}.json.
 *  Spouští se jen když routeKey není null (otevřený detail trasy). */
export function useRouteDetail(routeKey: string | null): RouteDetailState {
  const [state, setState] = useState<RouteDetailState>({
    history: null, calendar: null, loading: false, error: null,
  });

  useEffect(() => {
    if (!routeKey) {
      setState({ history: null, calendar: null, loading: false, error: null });
      return;
    }
    let cancelled = false;
    setState({ history: null, calendar: null, loading: true, error: null });
    (async () => {
      try {
        const [history, calendar] = await Promise.all([
          fetchJson<HistoryFile>(`data/history/${routeKey}.json`),
          fetchJson<CalendarFile>(`data/calendar/${routeKey}.json`).catch(() => [] as CalendarFile),
        ]);
        if (!cancelled) setState({ history, calendar, loading: false, error: null });
      } catch (err) {
        if (!cancelled) setState({
          history: null, calendar: null, loading: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    })();
    return () => { cancelled = true; };
  }, [routeKey]);

  return state;
}
