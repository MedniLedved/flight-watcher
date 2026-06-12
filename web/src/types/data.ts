/**
 * Datový kontrakt mezi scanner.py (producent, viz src/exporter.py) a
 * dashboardem (konzument). Jediný zdroj pravdy – struktura přesně kopíruje
 * JSONy zapisované exportem na konci scanu.
 *
 * Měna je vždy EUR (neukládá se). `date`/`observedDate` = den POZOROVÁNÍ,
 * data letu jsou zvlášť (`departDate`/`returnDate`). Všechna data jsou ISO
 * řetězce YYYY-MM-DD, časy ISO 8601.
 */

export type RouteType = "roundtrip" | "openjaw";

/** route_key: `{origin}-{destination}-roundtrip` (3 segmenty) nebo
 *  `{origin}-{destination}-{returnOrigin}-openjaw` (4 segmenty).
 *  origin/destination můžou být city kódy (TYO, OSA). */
export type RouteKey = string;

// ---------------------------------------------------------------------------
// data/latest.json — aktuální nejlepší nabídky, zapisuje se in-process,
// takže obsahuje i efemérní pole ze živého scanu.
// ---------------------------------------------------------------------------
export interface LatestOfferFlags {
  isNewLow: boolean;
  /** price - last_price (baseline ze scanneru); null bez předchozí ceny */
  priceDeltaEur: number | null;
  /** změna vs. nejlepší cena před ≥7 dny; null dokud není 7 dní dat */
  pctChange7d: number | null;
  isBigDrop: boolean;
}

export interface LatestOffer {
  routeKey: RouteKey;
  type: RouteType;
  origin: string;
  destination: string;
  /** vyplněné jen u openjaw */
  returnOrigin: string | null;
  returnDestination: string | null;
  /** EUR */
  price: number;
  source: string;
  departDate: string | null;
  returnDate: string | null;
  nights: number | null;
  /** EFEMÉRNÍ – jen z živého scanu, v historických řadách neexistuje */
  airlines: string[];
  /** EFEMÉRNÍ – přímý odkaz na deal */
  dealUrl: string | null;
  /** den pozorování (dnešek běhu) */
  observedDate: string;
  flags: LatestOfferFlags;
}

export type LatestFile = LatestOffer[];

// ---------------------------------------------------------------------------
// data/history/{route_key}.json — kanonická dlouhodobá řada, append-only,
// nikdy se neprořezává. Bez efemérních polí.
// ---------------------------------------------------------------------------
export interface HistoryRecord {
  /** den pozorování */
  date: string;
  /** EUR */
  price: number;
  source: string;
  departDate?: string;
  returnDate?: string;
}

export type HistoryFile = HistoryRecord[];

// ---------------------------------------------------------------------------
// data/calendar/{route_key}.json — aktuální nejlepší cena per odletový den.
// ---------------------------------------------------------------------------
export interface CalendarDay {
  departDate: string;
  returnDate: string | null;
  price: number;
  source: string;
  observedDate: string;
}

export type CalendarFile = CalendarDay[];

// ---------------------------------------------------------------------------
// data/stats.json — předpočítané agregáty per trasa.
// ---------------------------------------------------------------------------
export interface RouteStats {
  /** ze scanneru; přežívá 90denní prořez interní historie */
  allTimeMin: number | null;
  min90d: number | null;
  max90d: number | null;
  avg90d: number | null;
  /** % změna denního minima v 30denním okně; null bez dat */
  trend30d: number | null;
  biggestDrop: { from: number; to: number; date: string } | null;
  lastPrice: number | null;
  currentVsAvgPct: number | null;
}

export type StatsFile = Record<RouteKey, RouteStats>;

// ---------------------------------------------------------------------------
// data/insights.json — cross-cutting analytika (sdílená s Telegram souhrnem).
// ---------------------------------------------------------------------------
export interface AirportInsight {
  code: string;
  dealRatePct: number | null;
  medianEur: number | null;
  observations: number;
}

export interface DowInsight {
  /** česká zkratka dne, velkými (PO…NE) */
  dow: string;
  dealRatePct: number | null;
  medianEur: number | null;
}

export interface InsightsFile {
  airportPriority: { europe: AirportInsight[]; japan: AirportInsight[] };
  cheapestDepartureDow: DowInsight[];
  cheapestArrivalDow: DowInsight[];
}

// ---------------------------------------------------------------------------
// data/routes.json — seznam tras + souřadnice pro mapu.
// ---------------------------------------------------------------------------
export interface LatLon {
  lat: number;
  lon: number;
}

export interface RouteInfo {
  routeKey: RouteKey;
  type: RouteType | string;
  origin: string;
  destination: string;
  returnOrigin: string | null;
  returnDestination: string | null;
  originName: string;
  destinationName: string;
  returnOriginName: string | null;
  coords: {
    origin: LatLon | null;
    destination: LatLon | null;
    returnOrigin: LatLon | null;
  };
}

export type RoutesFile = RouteInfo[];

// ---------------------------------------------------------------------------
// data/meta.json
// ---------------------------------------------------------------------------
export interface MetaFile {
  /** čas běhu exportu (ISO 8601, UTC) */
  lastScan: string;
  scanCount: number;
  schemaVersion: number;
  apiQuota: {
    skyscrapper: {
      remaining: number | null;
      limit: number | null;
      resetAt: string | null;
    };
    requestsThisMonth: Record<string, number>;
    disabledUntil: Record<string, string | null>;
  };
}

// ---------------------------------------------------------------------------
// config/agent.json — konfigurace agenta, editovatelná přes záložku Nastavení.
// ---------------------------------------------------------------------------
export interface AirportTransport {
  costEur: number;
  durationMin: number;
  /** "vlak/bus" | "auto" | "let" */
  mode: string;
  /** Jen pro mode="let": cena transferu centrum→letiště (EUR), výchozí 25 */
  airportTransferCostEur?: number;
  /** Jen pro mode="let": čas transferu centrum→letiště (hodiny), výchozí 2.5 */
  airportTransferTimeH?: number;
}

export interface AgentAirport {
  code: string;
  name: string;
  /** Zeměpisná šířka; pokud chybí/0, scanner doplní automaticky přes Nominatim */
  lat?: number;
  /** Zeměpisná délka; pokud chybí/0, scanner doplní automaticky přes Nominatim */
  lon?: number;
  priority: number;
  enabled: boolean;
  /** IATA metropolitní/city kód (např. MIL pro MXP) */
  cityCode?: string;
  transport?: AirportTransport;
}

export interface AgentConfig {
  homeLocation: string;
  travelWindow: { from: string; to: string };
  stayLength: { minNights: number; maxNights: number };
  europeAirports: AgentAirport[];
  japanAirports: AgentAirport[];
  cityAliases: Record<string, { name: string; lat: number; lon: number }>;
  alertThresholds: {
    dealMaxEur: number;
    bigDropPct: number;
    newLowSensitivityPct: number;
  };
  sources: {
    googleFlights: boolean;
    duffel: boolean;
    skyScrapper: boolean;
    serpApi: boolean;
    amadeus: boolean;
    travelpayouts: boolean;
    flightLabs: boolean;
    letsFG: boolean;
    rss: {
      secretFlying: boolean;
      cestujlevne: boolean;
      jacks: boolean;
      milesAndMore: boolean;
    };
  };
  telegramAlerts: {
    priceAlert: boolean;
    dealAlert: boolean;
    dailySummary: boolean;
  };
}
