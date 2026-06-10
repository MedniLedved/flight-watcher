/** Barevná škála ceny: green #10b981 → yellow #f59e0b → red #ef4444.
 *  Sdílí ji kalendářová heatmapa a swimlanes. */
export function priceColor(price: number, min: number, max: number): string {
  const t = max === min ? 0 : (price - min) / (max - min);
  if (t < 0.5) {
    const u = t * 2;
    return `rgb(${Math.round(16 + 229 * u)},${Math.round(185 - 27 * u)},${Math.round(129 - 118 * u)})`;
  }
  const u = (t - 0.5) * 2;
  return `rgb(${Math.round(245 - 6 * u)},${Math.round(158 - 90 * u)},${Math.round(11 + 57 * u)})`;
}
