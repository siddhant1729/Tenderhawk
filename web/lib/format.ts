import { now as serverNow } from "./clock";
import type { Excerpt, Verdict } from "./types";

/** 4000000 -> "₹40 L", 12000000 -> "₹1.2 Cr", 37000 -> "₹37,000" (same rules as api/matcher.inr). */
export function inr(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const trim = (n: number) => n.toFixed(2).replace(/\.?0+$/, "");
  if (v >= 1e7) return `₹${trim(v / 1e7)} Cr`;
  if (v >= 1e5) return `₹${trim(v / 1e5)} L`;
  return `₹${Math.round(v).toLocaleString("en-IN")}`;
}

/** "closes in 3 days", "closes in 5 h", "closed 2 days ago". */
export function deadline(endIso: string, now: Date = serverNow()): { text: string; closed: boolean; urgent: boolean } {
  const ms = new Date(endIso).getTime() - now.getTime();
  const abs = Math.abs(ms);
  const h = Math.round(abs / 36e5);
  const d = Math.round(abs / 864e5);
  const span = abs < 36e5 ? `${Math.max(1, Math.round(abs / 6e4))} min` : abs < 48 * 36e5 ? `${h} h` : `${d} days`;
  return ms >= 0
    ? { text: `closes in ${span}`, closed: false, urgent: ms < 48 * 36e5 }
    : { text: `closed ${span} ago`, closed: true, urgent: false };
}

export function istDate(iso: string): string {
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "numeric", minute: "2-digit",
  });
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  ELIGIBLE: "Eligible",
  CHECK_DOCS: "Check docs",
  NOT_ELIGIBLE: "Not eligible",
};

/** Split an excerpt into [before, highlighted clause, after], clamped to the text. */
export function splitExcerpt(ex: Excerpt): [string, string, string] {
  const a = Math.max(0, Math.min(ex.highlight_start, ex.text.length));
  const b = Math.max(a, Math.min(ex.highlight_end, ex.text.length));
  return [ex.text.slice(0, a), ex.text.slice(a, b), ex.text.slice(b)];
}

/** Only http(s) links from API data are rendered as links. */
export function safeHref(u: string | null | undefined): string | undefined {
  return u && /^https?:\/\//i.test(u) ? u : undefined;
}

/** Comma-separated input -> trimmed, non-empty list. */
export function splitList(s: string): string[] {
  return s.split(",").map((x) => x.trim()).filter(Boolean);
}

/** "1.2 Cr" / "40 lakh" / "12000000" -> rupees, or null. */
export function parseRupees(s: string): number | null {
  const t = s.toLowerCase().replace(/[₹,\s]/g, "");
  const m = t.match(/^(\d+(?:\.\d+)?)(cr|crore|l|lakh|lac|k)?$/);
  if (!m) return null;
  const n = parseFloat(m[1]);
  const mult = { cr: 1e7, crore: 1e7, l: 1e5, lakh: 1e5, lac: 1e5, k: 1e3 }[m[2] as "cr"] ?? 1;
  return Math.round(n * mult);
}

/** Excerpt for display: whitespace collapsed, at most `context` chars either side of the clause. */
export function compactExcerpt(ex: Excerpt, context = 160): { before: string; clause: string; after: string } {
  const squash = (s: string) => s.replace(/\s+/g, " ");
  const [a, b, c] = splitExcerpt(ex);
  let before = squash(a);
  let after = squash(c);
  const cutBefore = ex.prefix_truncated || before.length > context;
  const cutAfter = ex.suffix_truncated || after.length > context;
  if (before.length > context) before = before.slice(before.length - context).replace(/^\S*\s/, "");
  if (after.length > context) after = after.slice(0, context).replace(/\s\S*$/, "");
  return { before: (cutBefore ? "…" : "") + before.trimStart(), clause: squash(b).trim(), after: after.trimEnd() + (cutAfter ? "…" : "") };
}
