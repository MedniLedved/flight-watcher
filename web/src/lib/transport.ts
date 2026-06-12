import type { AgentConfig, AirportTransport } from "@/types/data";

export function getTransport(
  origin: string,
  agentConfig: AgentConfig | null,
): AirportTransport | null {
  if (!agentConfig) return null;
  return (
    agentConfig.europeAirports.find(
      (a) => a.code === origin || a.cityCode === origin,
    )?.transport ?? null
  );
}

/** Cena dopravy pro jeden konec open-jaw (jeden směr). */
function oneWayCost(t: AirportTransport | null): number {
  if (!t) return 0;
  if (t.mode === "let") return t.costEur + (t.airportTransferCostEur ?? 25);
  return t.costEur;
}

/** Effective price: base + transport cost (if toggle on).
 *  Open-jaw, různá EU letiště: každé letiště zvlášť —
 *    vlak/bus/auto → 1× costEur; let → 1× costEur (open-jaw) + 1× airportTransferCostEur.
 *  Roundtrip / open-jaw na japonské straně:
 *    mode="let" → costEurRoundtrip + 2× airportTransferCostEur; jinak 2× costEur. */
export function effectivePrice(
  price: number,
  origin: string,
  agentConfig: AgentConfig | null,
  includeTransport: boolean,
  returnDestination?: string | null,
): number {
  if (!includeTransport) return price;
  if (returnDestination && returnDestination !== origin) {
    const t1 = getTransport(origin, agentConfig);
    const t2 = getTransport(returnDestination, agentConfig);
    return price + oneWayCost(t1) + oneWayCost(t2);
  }
  const t = getTransport(origin, agentConfig);
  if (!t) return price;
  if (t.mode === "let") {
    return price + (t.costEurRoundtrip ?? t.costEur * 2) + 2 * (t.airportTransferCostEur ?? 25);
  }
  return price + 2 * t.costEur;
}

export function fmtDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h} h`;
  return `${h} h ${m} min`;
}
