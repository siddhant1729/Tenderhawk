import { VERDICT_LABEL } from "@/lib/format";
import type { Verdict } from "@/lib/types";

const STYLE: Record<Verdict, string> = {
  ELIGIBLE: "bg-ok-bg text-ok",
  CHECK_DOCS: "bg-warn-bg text-warn",
  NOT_ELIGIBLE: "bg-bad-bg text-bad",
};
const DOT: Record<Verdict, string> = { ELIGIBLE: "🟢", CHECK_DOCS: "🟠", NOT_ELIGIBLE: "🔴" };

export default function VerdictChip({ verdict, large = false }: { verdict: Verdict; large?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold whitespace-nowrap ${STYLE[verdict]} ${large ? "px-4 py-1.5 text-base" : "px-2.5 py-0.5 text-xs"}`}>
      <span aria-hidden>{DOT[verdict]}</span>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}
