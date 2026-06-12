import { ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { LatestOffer } from "@/types/data";
import { buildGoogleFlightsUrl, cleanDealUrl } from "@/lib/dealUrl";

interface Props {
  offer: LatestOffer;
  size?: "sm" | "default";
  /** compact = OffersTable (méně místa), default = SwimlanesView popup */
  compact?: boolean;
}

const INCOGNITO_HINT = "Pro nejnižší cenu otevři v anonymním okně (Ctrl+Shift+N / Cmd+Shift+N)";

export function DealButton({ offer, size = "sm", compact = false }: Props) {
  if (!offer.dealUrl) return null;

  const cleanedUrl = cleanDealUrl(offer.dealUrl);
  const gfUrl = buildGoogleFlightsUrl(offer);

  return (
    <span className="inline-flex items-center gap-1">
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
      <a
        href={gfUrl}
        target="_blank"
        rel="noreferrer noopener"
        title="Ověřit cenu na Google Flights (čistý dotaz, bez cookies)"
        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      >
        <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden>
          {/* Google Flights plane icon */}
          <path d="M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>
        </svg>
      </a>
    </span>
  );
}
