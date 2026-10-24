import { VERDICT_LABEL, verdictLabel } from "@/lib/format";
import type { Reason, Verdict } from "@/lib/types";

const STYLE: Record<Verdict, string> = {
  ELIGIBLE: "bg-ok-bg text-ok",
  CHECK_DOCS: "bg-warn-bg text-warn",
  NOT_ELIGIBLE: "bg-bad-bg text-bad",
};
const DOT: Record<Verdict, string> = { ELIGIBLE: "🟢", CHECK_DOCS: "🟠", NOT_ELIGIBLE: "🔴" };

/** Colour + icon + plain words ("You can bid", "Keep proof ready", "Turnover short"): never colour alone. */
export default function VerdictChip({ verdict, reasons = [], large = false }:
  { verdict: Verdict; reasons?: Reason[]; large?: boolean }) {
  return (
    <span title={VERDICT_LABEL[verdict]}
          className={`inline-flex items-center gap-1.5 rounded-full font-semibold whitespace-nowrap ${STYLE[verdict]} ${large ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-xs"}`}>
      <span aria-hidden>{DOT[verdict]}</span>
      {verdictLabel(verdict, reasons)}
      <span className="sr-only"> ({VERDICT_LABEL[verdict]})</span>
    </span>
  );
}
