/**
 * Validace a (de)serializace `config/agent.json` pro záložku Nastavení.
 * Validuje se proti TS typům (viz src/types/data.ts) PŘED commitem přes
 * GitHub API – do repa se nikdy nedostane rozbitý config.
 */

import type { AgentAirport, AgentConfig } from "@/types/data";

const VALID_MODES = ["vlak/bus", "auto", "let"] as const;

/** Normalizuje transport.mode: vlak* → "vlak/bus", neznámé → "vlak/bus". */
function normalizeMode(mode: string): string {
  if ((VALID_MODES as readonly string[]).includes(mode)) return mode;
  if (mode.toLowerCase().startsWith("vlak") || mode.toLowerCase() === "bus") return "vlak/bus";
  return mode; // ponecháme; validace pak ukáže chybu
}

export function cloneConfig(config: AgentConfig): AgentConfig {
  const clone = structuredClone(config);
  for (const a of clone.europeAirports ?? []) {
    if (a.transport) a.transport.mode = normalizeMode(a.transport.mode);
  }
  return clone;
}

export function emptyEuropeAirport(priority: number): AgentAirport {
  return {
    code: "",
    name: "",
    priority,
    enabled: true,
    transport: { costEur: 0, durationMin: 0, mode: "vlak/bus" },
  };
}

export function emptyJapanAirport(priority: number): AgentAirport {
  return { code: "", name: "", priority, enabled: true };
}

const IATA_RE = /^[A-Z]{3}$/;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function isFiniteNumber(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function validateAirport(
  a: AgentAirport,
  label: string,
  requireTransport: boolean,
  errors: string[],
): void {
  if (!IATA_RE.test(a.code)) {
    errors.push(`${label}: kód „${a.code || "(prázdný)"}" musí být 3 velká písmena (IATA).`);
  }
  if (!a.name.trim()) errors.push(`${label}: chybí název.`);
  if (!Number.isInteger(a.priority) || a.priority < 1) {
    errors.push(`${label}: priorita musí být celé číslo ≥ 1.`);
  }
  if (typeof a.enabled !== "boolean") errors.push(`${label}: „enabled" musí být boolean.`);
  if (requireTransport) {
    const t = a.transport;
    if (!t) {
      errors.push(`${label}: chybí údaje o dopravě.`);
    } else {
      if (!isFiniteNumber(t.costEur) || t.costEur < 0) {
        errors.push(`${label}: cena dopravy musí být číslo ≥ 0.`);
      }
      if (!isFiniteNumber(t.durationMin) || t.durationMin < 0) {
        errors.push(`${label}: doba dopravy musí být číslo ≥ 0 min.`);
      }
      const validModes = ["vlak/bus", "auto", "let"];
      if (!validModes.includes(t.mode)) {
        errors.push(`${label}: prostředek dopravy musí být vlak/bus, auto nebo let.`);
      }
    }
  }
}

/** Vrátí seznam chyb; prázdný = config je validní. */
export function validateAgentConfig(config: AgentConfig): string[] {
  const errors: string[] = [];

  if (!config.homeLocation.trim()) errors.push("Výchozí lokace (homeLocation) nesmí být prázdná.");

  const { from, to } = config.travelWindow;
  if (!ISO_DATE_RE.test(from)) errors.push("Cestovní okno: „od“ musí být datum YYYY-MM-DD.");
  if (!ISO_DATE_RE.test(to)) errors.push("Cestovní okno: „do“ musí být datum YYYY-MM-DD.");
  if (ISO_DATE_RE.test(from) && ISO_DATE_RE.test(to) && from > to) {
    errors.push("Cestovní okno: „od“ musí být dříve než „do“.");
  }

  const { minNights, maxNights } = config.stayLength;
  if (!Number.isInteger(minNights) || minNights < 0) {
    errors.push("Délka pobytu: min nocí musí být celé číslo ≥ 0.");
  }
  if (!Number.isInteger(maxNights) || maxNights < 0) {
    errors.push("Délka pobytu: max nocí musí být celé číslo ≥ 0.");
  }
  if (Number.isInteger(minNights) && Number.isInteger(maxNights) && minNights > maxNights) {
    errors.push("Délka pobytu: min nocí nesmí být větší než max.");
  }

  if (config.europeAirports.length === 0) errors.push("Musí být aspoň jedno evropské letiště.");
  if (config.japanAirports.length === 0) errors.push("Musí být aspoň jedno japonské letiště.");

  config.europeAirports.forEach((a, i) =>
    validateAirport(a, `Evropské letiště #${i + 1} (${a.code || "?"})`, true, errors),
  );
  config.japanAirports.forEach((a, i) =>
    validateAirport(a, `Japonské letiště #${i + 1} (${a.code || "?"})`, false, errors),
  );

  const dupe = (list: AgentAirport[]) =>
    list.map((a) => a.code).filter((c, i, arr) => c && arr.indexOf(c) !== i);
  const euDupes = [...new Set(dupe(config.europeAirports))];
  const jpDupes = [...new Set(dupe(config.japanAirports))];
  if (euDupes.length) errors.push(`Duplicitní evropská letiště: ${euDupes.join(", ")}.`);
  if (jpDupes.length) errors.push(`Duplicitní japonská letiště: ${jpDupes.join(", ")}.`);

  const th = config.alertThresholds;
  if (!isFiniteNumber(th.dealMaxEur) || th.dealMaxEur < 0) {
    errors.push("Práh dealu (max cena) musí být číslo ≥ 0.");
  }
  if (!isFiniteNumber(th.bigDropPct) || th.bigDropPct < 0 || th.bigDropPct > 100) {
    errors.push("Práh „velkého poklesu“ musí být 0…100 %.");
  }
  if (
    !isFiniteNumber(th.newLowSensitivityPct) ||
    th.newLowSensitivityPct < 0 ||
    th.newLowSensitivityPct > 100
  ) {
    errors.push("Citlivost „nového minima“ musí být 0…100 %.");
  }

  return errors;
}

/** Serializace do stejného formátu jako commitnutý soubor (2 mezery + LF). */
export function serializeConfig(config: AgentConfig): string {
  return JSON.stringify(config, null, 2) + "\n";
}
