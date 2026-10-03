"use client";

/**
 * Jump, wired to the workspace (design-direction §6.3, §6.4 rule 4). It lists only:
 *   - the destinations this person's capabilities show (the same model as the masthead);
 *   - their own recent questions, from GET /v1/activity (fetched while Jump is open);
 *   - for administrators, members matching what they type, from GET
 *     /v1/admin/access/members -- the search the Access page already runs.
 * Nothing is searched that those endpoints don't already return to this person, and
 * there is no document or title search (security-model §10).
 */
import { CornerDownRight, History, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useAccessMembers, useActivity } from "@/api/queries";
import { questionStatus, traceHref } from "@/components/activity/questions";
import { Jump, type JumpGroupStatus, type JumpItem } from "@/components/primitives/Jump";
import { formatDay } from "@/lib/format";

import { visibleNav, visibleSubpages } from "./nav";
import { useWorkspace } from "./workspace-context";

const GO = "Go to";
const RECENT = "Your recent questions";
const MEMBERS = "Members";
/** Recent questions listed before anything is typed; typing searches every loaded one. */
const RECENT_IDLE = 5;
/** Server-side member search starts at two characters (as on the Access page). */
const MEMBER_MIN = 2;

export function ShellJump({
  open,
  onOpenChange,
  onGo,
  onAsk,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Navigate to a destination (the shell moves focus to the page). */
  onGo: (href: string) => void;
  onAsk: (question: string) => void;
}) {
  const { me, isAdmin } = useWorkspace();
  const [query, setQuery] = useState("");
  const [settled, setSettled] = useState("");

  // Debounce the member search so each keystroke doesn't become a request.
  useEffect(() => {
    const t = setTimeout(() => setSettled(query.trim()), 200);
    return () => clearTimeout(t);
  }, [query]);
  const memberQuery = query.trim().length >= MEMBER_MIN && settled.length >= MEMBER_MIN ? settled : "";

  const recent = useActivity(open);
  const members = useAccessMembers(memberQuery, open && isAdmin && memberQuery.length >= MEMBER_MIN);

  const items = useMemo(() => {
    const out: JumpItem[] = [];
    for (const { section, destinations } of visibleNav(me)) {
      for (const d of destinations) {
        out.push({ id: `go:${d.href}`, group: GO, label: d.label, icon: d.icon, keywords: section.label, onSelect: () => onGo(d.href) });
      }
    }
    for (const p of visibleSubpages(me)) {
      const parent = visibleNav(me)
        .flatMap((s) => s.destinations)
        .find((d) => d.href === p.parent);
      out.push({ id: `go:${p.href}`, group: GO, label: p.label, detail: parent?.label, icon: CornerDownRight, onSelect: () => onGo(p.href) });
    }

    const questions = recent.data?.pages.flatMap((page) => page.items) ?? [];
    const typed = query.trim().length > 0;
    for (const q of typed ? questions : questions.slice(0, RECENT_IDLE)) {
      out.push({
        id: `trace:${q.trace_id}`,
        group: RECENT,
        label: q.question,
        detail: [questionStatus(q), formatDay(q.created_at)].filter(Boolean).join(" · "),
        icon: History,
        onSelect: () => onGo(traceHref(q)),
      });
    }

    if (isAdmin && memberQuery) {
      for (const m of members.data?.pages[0]?.items ?? []) {
        out.push({
          id: `member:${m.id}`,
          group: MEMBERS,
          label: m.name,
          detail: m.email,
          icon: UserRound,
          onSelect: () => onGo(`/access/members/${m.id}`),
        });
      }
    }
    return out;
  }, [me, isAdmin, recent.data, members.data, memberQuery, query, onGo]);

  const statuses: JumpGroupStatus[] = [];
  if (recent.isPending && recent.fetchStatus === "fetching") statuses.push({ group: RECENT, message: "Loading your recent questions…" });
  else if (recent.isError) statuses.push({ group: RECENT, message: "Couldn't load your recent questions." });
  if (isAdmin && memberQuery) {
    if (members.isFetching) statuses.push({ group: MEMBERS, message: "Searching members…" });
    else if (members.isError) statuses.push({ group: MEMBERS, message: "Couldn't search members." });
  }

  return (
    <Jump
      open={open}
      onOpenChange={onOpenChange}
      items={items}
      groups={[GO, RECENT, MEMBERS]}
      statuses={statuses}
      onAsk={onAsk}
      onQueryChange={setQuery}
      placeholder={isAdmin ? "Go to a page, a recent question or a member, or ask…" : "Go to a page or a recent question, or ask…"}
    />
  );
}
