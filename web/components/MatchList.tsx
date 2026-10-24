"use client";

import Link from "next/link";
import { useState } from "react";
import { deadline, inr } from "@/lib/format";
import type { MatchSummary } from "@/lib/types";
import VerdictChip from "./VerdictChip";

function Row({ m }: { m: MatchSummary }) {
  const t = m.tender;
  const d = deadline(t.end_date);
  const top = m.reasons.filter((r) => r.kind !== "info").slice(0, 2);
  return (
    <li>
      <Link href={`/matches/${m.id}`}
            className="flex flex-col gap-2 rounded-xl border border-line bg-card p-4 hover:border-ink/40 md:flex-row md:items-center md:gap-4">
        <div className="flex items-center gap-2 md:w-36 md:flex-col md:items-start">
          <VerdictChip verdict={m.verdict} reasons={m.reasons} />
          <span className="rounded bg-paper-2 px-1.5 py-0.5 text-xs font-semibold uppercase">{t.portal}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold" title={t.title}>{t.title}</p>
          <p className="truncate text-sm text-ink-soft">{t.buyer || "Buyer not listed"}</p>
          {top.length > 0 && <p className="mt-1 truncate text-sm">{top.map((r) => r.text).join(" · ")}</p>}
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm md:w-64 md:justify-end">
          <span className="font-semibold">{inr(t.est_value)}</span>
          <span className={d.closed ? "text-bad" : d.urgent ? "font-semibold text-warn" : ""}>{d.text}</span>
          {m.fit_score != null && <span className="text-ink-soft" title="Product fit">fit {Math.round(m.fit_score * 100)}%</span>}
        </div>
        {m.alerted_at && <span className="text-xs text-ok">Alert sent</span>}
      </Link>
    </li>
  );
}

export default function MatchList({ matches }: { matches: MatchSummary[] }) {
  const [showLow, setShowLow] = useState(false);
  const relevant = matches.filter((m) => m.relevant);
  const low = matches.filter((m) => !m.relevant);
  const counts = relevant.reduce<Record<string, number>>((a, m) => ({ ...a, [m.verdict]: (a[m.verdict] || 0) + 1 }), {});

  if (!matches.length) {
    return <p className="rounded-xl border border-dashed border-line p-6 text-center text-ink-soft">No matches yet. Run a sweep.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-ink-soft">
        {relevant.length} relevant: {counts.ELIGIBLE || 0} you can bid on, {counts.CHECK_DOCS || 0} need proof ready,{" "}
        {counts.NOT_ELIGIBLE || 0} you can’t bid on
      </p>
      {relevant.length ? (
        <ul className="flex flex-col gap-2">{relevant.map((m) => <Row key={m.id} m={m} />)}</ul>
      ) : (
        <p className="rounded-xl border border-dashed border-line p-6 text-center text-ink-soft">
          No tenders matched your products in this sweep.
        </p>
      )}
      {low.length > 0 && (
        <div className="flex flex-col gap-2">
          <button onClick={() => setShowLow(!showLow)} className="self-start text-sm font-semibold text-ink-soft underline">
            {showLow ? "Hide" : "Show"} {low.length} low-relevance tender{low.length === 1 ? "" : "s"}
          </button>
          {showLow && <ul className="flex flex-col gap-2 opacity-80">{low.map((m) => <Row key={m.id} m={m} />)}</ul>}
        </div>
      )}
    </div>
  );
}
