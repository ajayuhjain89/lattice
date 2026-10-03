/**
 * One concept, three places: a question you asked, as Activity, Jump and the Ask desk
 * show it -- its status word, its date, its trace (re-authorized when opened), and *Ask
 * again*, which puts it back in the composer unsent.
 */
import type { OwnActivity } from "@/api/types";
import { shortStatus } from "@/components/shell/status-labels";

export const traceHref = (q: Pick<OwnActivity, "trace_id">) => `/traces/${q.trace_id}`;

export const questionStatus = (q: Pick<OwnActivity, "status">) => shortStatus(q.status);
