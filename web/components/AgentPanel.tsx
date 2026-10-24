import { safeHref } from "@/lib/format";
import { AGENT_CARDS, type CardStatus, type SweepState } from "@/lib/sweep";

const STATUS: Record<CardStatus, { label: string; cls: string; dot: string }> = {
  idle: { label: "Idle", cls: "text-ink-soft", dot: "bg-line" },
  running: { label: "Running", cls: "text-info", dot: "bg-info animate-pulse" },
  done: { label: "Done", cls: "text-ok", dot: "bg-ok" },
  error: { label: "Needs you", cls: "text-bad", dot: "bg-bad" },
};

/** The live agents panel: one card per TinyFish agent, driven by the sweep's event stream. */
export default function AgentPanel({ state }: { state: SweepState }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {AGENT_CARDS.map(({ key, label, hint }) => {
          const c = state.cards[key];
          const st = STATUS[c.status];
          const href = safeHref(c.directUrl);
          return (
            <div key={key} className="flex flex-col gap-2 rounded-xl border border-line bg-card p-4" aria-live="polite">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 font-semibold">
                  <span className={`h-2.5 w-2.5 rounded-full ${st.dot}`} aria-hidden />
                  {label}
                </span>
                <span className={`text-xs font-semibold uppercase ${st.cls}`}>{st.label}</span>
              </div>
              <div className="text-xs text-ink-soft">{hint}</div>
              <div className="flex items-baseline gap-4">
                <span><b className="text-2xl tabular-nums">{c.steps}</b> <span className="text-sm text-ink-soft">steps</span></span>
                {c.count != null && (
                  <span><b className="text-2xl tabular-nums">{c.count}</b>{" "}
                    <span className="text-sm text-ink-soft">{key === "corrigenda" ? "new" : "bids"}</span></span>
                )}
              </div>
              <p className="line-clamp-3 min-h-[3.75rem] text-sm text-ink-soft">{c.message}</p>
              {c.status === "error" && href && (
                <a href={href} target="_blank" rel="noopener noreferrer"
                   className="self-start rounded-md bg-ink px-3 py-1 text-sm font-semibold text-paper">
                  Open on portal ↗
                </a>
              )}
            </div>
          );
        })}
      </div>
      {(state.pipeline || state.summary || state.error) && (
        <div className="rounded-xl bg-paper-2 px-4 py-2 text-sm">
          {state.error && <p role="alert" className="text-bad">{state.error}</p>}
          {state.pipeline && <p><b>Working:</b> {state.pipeline}…</p>}
          {state.summary && <p><b>Done.</b> {state.summary}</p>}
          {state.background && <p role="status"><b>Still reading:</b> {state.background}…</p>}
        </div>
      )}
    </div>
  );
}
