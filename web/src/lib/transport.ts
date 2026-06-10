import type { AgentConfig, AirportTransport } from "@/types/data";

export function getTransport(
  origin: string,
  agentConfig: AgentConfig | null,
): AirportTransport | null {
  if (!agentConfig) return null;
  return agentConfig.europeAirports.find((a) => a.code === origin)?.transport ?? null;
}

/** Effective price: base + 2× round-trip transport cost (if toggle on). */
export function effectivePrice(
  price: number,
  origin: string,
  agentConfig: AgentConfig | null,
  includeTransport: boolean,
): number {
  if (!includeTransport) return price;
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
