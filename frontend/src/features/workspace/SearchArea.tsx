import { MapPinned, SquareDashed, TriangleAlert } from "lucide-react";
import { isUnavailable } from "../../api/client";
import { useArea, useCountries } from "../../api/queries";
import type { Case, SearchArea } from "../../api/types";
import { Button } from "../../components/ui/Button";
import { cn } from "../../lib/cn";
import { fmtInt } from "../../lib/format";
import { setTab, ws } from "../../state/workspace";
import { useCaseData } from "./caseContext";

export function caseCountries(c: Case): string[] {
  return c.countries ?? [];
}

/** Region or countries set: enough to search. */
export function hasSearchArea(c: Case): boolean {
  return !!c.region || caseCountries(c).length > 0;
}

/** The case's search area (null on servers without the countries feature). */
export function useSearchArea(): { area: SearchArea | null; tooLarge: boolean } {
  const c = useCaseData();
  const q = useArea(c);
  if (q.error && isUnavailable(q.error)) return { area: null, tooLarge: false };
  return { area: q.data ?? null, tooLarge: !!q.data?.too_large };
}

export const TOO_LARGE_REASON = "Draw a region inside the country first: it is too large to search as a whole";

export function fmtArea(km2: number): string {
  return `${fmtInt(km2)} km²`;
}

/** Land area of the search area; the server reports 0 when it's too large, so fall back to the countries' own areas. */
export function useAreaKm2(area: SearchArea | null): number | null {
  const countries = useCountries(!!area?.countries.length);
  if (!area) return null;
  if (area.area_km2 > 0) return area.area_km2;
  const byCode = new Map((countries.data ?? []).map((k) => [k.code, k.area_km2]));
  const sum = area.countries.reduce((a, code) => a + (byCode.get(code) ?? 0), 0);
  return sum > 0 ? sum : null;
}

/** One line saying what a search or download will cover; a clear prompt when the area is too large. */
export function SearchAreaBanner({ className }: { className?: string }) {
  const { area, tooLarge } = useSearchArea();
  const km2 = useAreaKm2(area);
  if (!area) return null;
  if (tooLarge)
    return (
      <div className={cn("flex items-start gap-2.5 rounded-lg border border-warn/30 bg-warn/8 px-3 py-2.5", className)}>
        <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1 text-xs">
          <div className="font-medium text-fg">Draw a region inside {area.description}</div>
          <div className="mt-0.5 text-fg-muted">
            {area.description} {km2 ? `covers ${fmtArea(km2)}, too much` : "is too large"} to search as a whole. Searches and downloads are off until you draw a
            region inside it.
          </div>
          <Button
            size="xs"
            variant="secondary"
            icon={<SquareDashed />}
            className="mt-2"
            onClick={() => {
              setTab("setup");
              ws.set({ mapTool: "region" });
            }}
          >
            Draw region
          </Button>
        </div>
      </div>
    );
  if (!area.bbox) return null;
  return (
    <div className={cn("flex items-center gap-2 rounded-md border border-line bg-surface-2/60 px-2.5 py-1.5 text-xs", className)} data-tip="Searches and downloads cover this area. Change it in Setup.">
      <MapPinned className="size-3.5 shrink-0 text-fg-faint" />
      <span className="min-w-0 flex-1 truncate text-fg-muted">
        Search area: <span className="text-fg">{area.description}</span>
      </span>
      {km2 != null && <span className="shrink-0 text-2xs text-fg-faint tnum">{fmtArea(km2)}</span>}
    </div>
  );
}
