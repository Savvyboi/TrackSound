import { useEffect, useRef } from "react";
import { useAircraftEvents } from "../../api/queries";
import { ws } from "../../state/workspace";
import { useCaseData } from "../workspace/caseContext";
import { suggestPair } from "./actions";
import { EventsSection } from "./EventsSection";
import { FlightsSection } from "./FlightsSection";
import { ResultsSection } from "./ResultsSection";
import { SearchSection } from "./SearchSection";

export function AircraftTab() {
  const c = useCaseData();
  const events = useAircraftEvents(c.id);
  const suggested = useRef(false);

  // pre-select the suggested search pair once, if the user hasn't picked any
  useEffect(() => {
    if (suggested.current || !events.data?.length) return;
    suggested.current = true;
    const s = ws.get();
    if (s.checkedEventIds.length) return;
    const pair = suggestPair(events.data);
    if (pair) ws.set({ checkedEventIds: pair.map((e) => e.id) });
  }, [events.data]);

  return (
    <div>
      <EventsSection />
      <FlightsSection />
      <SearchSection />
      <ResultsSection />
    </div>
  );
}
