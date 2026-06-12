import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { LatestOffer } from "@/types/data";
import { buildGoogleFlightsUrl, buildKayakUrl, cleanDealUrl, ITA_MATRIX_URL } from "@/lib/dealUrl";

interface Props {
  offer: LatestOffer;
  size?: "sm" | "default";
  /** compact = OffersTable (méně místa), default = SwimlanesView popup */
  compact?: boolean;
}

const INCOGNITO_HINT = "Pro nejnižší cenu otevři v anonymním okně (Ctrl+Shift+N / Cmd+Shift+N)";

/** Malé ikonové tlačítko pro alternativní vyhledávač */
function VerifyLink({ href, title, children }: { href: string; title: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      title={title}
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
    >
      {children}
    </a>
  );
}

export function DealButton({ offer, size = "sm", compact = false }: Props) {
  if (!offer.dealUrl) return null;

  const cleanedUrl = cleanDealUrl(offer.dealUrl);
  const gfUrl = buildGoogleFlightsUrl(offer);
  const kayakUrl = buildKayakUrl(offer);

  return (
    <span className="inline-flex items-center gap-1">
      {/* Hlavní deal odkaz — tracking params odstraněny, noreferrer */}
      <Button asChild variant={compact ? "ghost" : "outline"} size={size}>
        <a
          href={cleanedUrl}
          target="_blank"
          rel="noreferrer noopener"
          title={INCOGNITO_HINT}
        >
          {!compact && <ExternalLink className="mr-1.5 h-3.5 w-3.5" />}
          {compact ? <>Deal <ExternalLink className="h-3 w-3" /></> : "Otevřít deal"}
        </a>
      </Button>

      {/* Google Flights — TFS protobuf deep-link, čistý dotaz bez personalizace */}
      <VerifyLink href={gfUrl} title="Ověřit na Google Flights (čistý dotaz, bez cookies)">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
          <path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>
        </svg>
      </VerifyLink>

      {/* Kayak — deep-link s trasou a daty, nezávislé GDS ověření */}
      <VerifyLink href={kayakUrl} title="Ověřit na Kayak (předvyplněno trasou a daty)">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          {/* Pádlo / veslo — symbol Kayak */}
          <line x1="12" y1="2" x2="12" y2="22" />
          <path d="M5 8 Q12 5 19 8" />
          <path d="M5 16 Q12 19 19 16" />
        </svg>
      </VerifyLink>

      {/* ITA Matrix — homepage (nepodporuje URL deep-link; GF je stejný engine s pre-fill) */}
      <VerifyLink href={ITA_MATRIX_URL} title="Otevřít ITA Matrix (bez předvyplnění — vyplň ručně; engine = základ Google Flights)">
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
          {/* Mřížka / matice */}
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
      </VerifyLink>
    </span>
  );
}
