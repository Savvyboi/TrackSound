import type { QueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../../api/client";
import { casePath, setCaseData } from "../../api/queries";
import type { Case, StartSource } from "../../api/types";

/**
 * PATCH the case start time with the source that produced it. Servers that don't know the
 * "alerts" source yet reject it (422): then it is stored as "user".
 */
export async function adoptStartTime(qc: QueryClient, caseId: string, startUtc: number, source: StartSource): Promise<Case> {
  const send = (src: StartSource) => api.patch<Case>(casePath(caseId), { start_utc: startUtc, start_utc_source: src });
  let updated: Case;
  try {
    updated = await send(source);
  } catch (e) {
    if (source === "alerts" && e instanceof ApiError && e.status === 422) updated = await send("user");
    else throw e;
  }
  setCaseData(qc, updated);
  return updated;
}
