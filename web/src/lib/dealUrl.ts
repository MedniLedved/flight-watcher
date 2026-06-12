import type { LatestOffer } from "@/types/data";

// Parametry, které ovlivňují personalizaci / sledování uživatele
const TRACKING_PARAMS = [
  "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
  "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
  "fbclid", "gclid", "msclkid", "ttclid", "twclid", "li_fat_id",
  "clickid", "click_id", "affid", "aff_id", "sid", "ref", "referrer", "referer",
  "aid", "affiliate_id", "partner_id", "tracking_id", "dclid", "wbraid", "gbraid",
];

export function cleanDealUrl(url: string): string {
  try {
    const u = new URL(url);
    TRACKING_PARAMS.forEach(p => u.searchParams.delete(p));
    return u.toString();
  } catch {
    return url;
  }
}

// ---------------------------------------------------------------------------
// Google Flights deep-link přes `tfs` (URL-safe base64 protobuf)
// Stejné kódování jako SettingsPage.tsx, rozšířeno o zpáteční let
// ---------------------------------------------------------------------------
function gfVarint(n: number): number[] {
  const out: number[] = [];
  for (;;) {
    const b = n & 0x7f;
    n >>>= 7;
    if (n) out.push(b | 0x80);
    else { out.push(b); break; }
  }
  return out;
}
function gfField(field: number, data: number[]): number[] {
  return [...gfVarint((field << 3) | 2), ...gfVarint(data.length), ...data];
}
function gfVarintField(field: number, value: number): number[] {
  return [...gfVarint((field << 3) | 0), ...gfVarint(value)];
}
function gfAirport(code: string): number[] {
  return gfField(2, [...new TextEncoder().encode(code)]);
}
function gfFlightData(origins: string[], dests: string[], date?: string | null): number[] {
  const fd: number[] = [];
  if (date) fd.push(...gfField(2, [...new TextEncoder().encode(date)]));
  for (const o of origins) fd.push(...gfField(13, gfAirport(o)));
  for (const d of dests) fd.push(...gfField(14, gfAirport(d)));
  return fd;
}

export function buildGoogleFlightsUrl(offer: LatestOffer): string {
  const origin = offer.origin;
  const dest = offer.destination;
  // Open-jaw: zpáteční let z jiného letiště
  const retOrigin = offer.returnOrigin ?? dest;
  const retDest = offer.returnDestination ?? origin;

  const hasReturn = offer.returnDate != null;
  const isRoundtrip = offer.type === "roundtrip" || hasReturn;

  const info: number[] = [
    ...gfField(3, gfFlightData([origin], [dest], offer.departDate)),
  ];
  if (isRoundtrip && hasReturn) {
    info.push(...gfField(3, gfFlightData([retOrigin], [retDest], offer.returnDate)));
  }
  info.push(
    ...gfField(8, [0x01]),
    ...gfVarintField(9, 1),                        // economy
    ...gfVarintField(19, isRoundtrip && hasReturn ? 1 : 2), // 1=roundtrip, 2=one-way
  );

  const b64 = btoa(String.fromCharCode(...info))
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
  return `https://www.google.com/travel/flights?tfs=${b64}&hl=cs`;
}

// ---------------------------------------------------------------------------
// Kayak — spolehlivý deep-link s přednastavenou trasou a daty
// Formát: /flights/{ORIGIN}-{DEST}/{DEPART}/{RETURN} (roundtrip)
//         /flights/{ORIGIN}-{DEST}/{DEPART}          (one-way)
// ---------------------------------------------------------------------------
export function buildKayakUrl(offer: LatestOffer): string {
  const from = offer.origin;
  const to = offer.destination;
  const depart = offer.departDate ?? "";
  const ret = offer.returnDate;

  if (!depart) return "https://www.kayak.com/flights";

  const legs = ret
    ? `${from}-${to}/${depart}/${ret}`
    : `${from}-${to}/${depart}`;
  return `https://www.kayak.com/flights/${legs}`;
}

// ---------------------------------------------------------------------------
// ITA Matrix — nemá URL deep-link; otevíráme homepage pro manuální ověření.
// Zachováváme jako referenci: Google Flights (buildGoogleFlightsUrl) je
// technický nástupce stejného enginu a podporuje pre-fill.
// ---------------------------------------------------------------------------
export const ITA_MATRIX_URL = "https://matrix.itasoftware.com/";
