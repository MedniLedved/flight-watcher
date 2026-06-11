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

/** Effective price: base + transport cost (if toggle on).
 *  Roundtrip: +2× transport(origin).
 *  Open-jaw s různým returnDestination: +transport(origin) + transport(returnDestination). */
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
    return price + (t1?.costEur ?? 0) + (t2?.costEur ?? 0);
  }
  const t = getTransport(origin, agentConfig);
  return price + (t ? 2 * t.costEur : 0);
}

export function fmtDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m} min`;
  if (m === 0) return `${h} h`;
  return `${h} h ${m} min`;
}
