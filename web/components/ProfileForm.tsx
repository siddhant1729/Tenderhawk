"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, ApiError } from "@/lib/api";
import { inr, parseRupees } from "@/lib/format";
import { EMPTY, type FormState, toProfileInput } from "@/lib/profileForm";

const CERTS = ["Udyam", "MSE", "Startup India", "ISO 9001", "GeM Seller"];

// Mirrors delivery/seed/ramesh.json (the demo persona).
const RAMESH = {
  name: "Ramesh Steel Fabricators",
  phone: "+919800000000",
  email: "ramesh@example.com",
  products: "steel storage racks, slotted angle racks, steel almirah, fabricated steel structures, industrial shelving",
  keywords: "steel rack, steel almirah, fabricated structure",
  hsn: "9403, 7308, 7326",
  turnover: "1.2 Cr",
  years: "8",
  certs: ["Udyam", "MSE", "GeM Seller"],
  otherCerts: "",
  states: "Haryana, Delhi, Uttar Pradesh, Rajasthan",
};

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-sm font-semibold">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-soft">{hint}</span>}
    </label>
  );
}

const input = "rounded-lg border border-line bg-white px-3 py-2 focus:outline-2 focus:outline-hawk";

export default function ProfileForm() {
  const router = useRouter();
  const [f, setF] = useState<FormState>(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof FormState) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const turnoverPreview = f.turnover.trim() ? parseRupees(f.turnover) : null;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const { input: body, error: err } = toProfileInput(f);
    if (!body) return setError(err ?? "Check the form.");
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createProfile(body);
      router.push(`/profiles/${id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn’t save the profile.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5 rounded-2xl border border-line bg-card p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-bold">Your business, once</h2>
        <button type="button" onClick={() => { setF(RAMESH); setError(null); }}
                className="rounded-lg border border-ink/20 px-3 py-1.5 text-sm font-semibold hover:bg-paper-2">
          Fill with demo: Ramesh, steel fabrication
        </button>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Business name"><input className={input} value={f.name} onChange={set("name")} required /></Field>
        <Field label="WhatsApp number" hint="With country code, e.g. +91…"><input className={input} value={f.phone} onChange={set("phone")} /></Field>
        <Field label="Email"><input className={input} type="email" value={f.email} onChange={set("email")} /></Field>
      </div>
      <Field label="Products you supply" hint="Comma-separated">
        <input className={input} value={f.products} onChange={set("products")} />
      </Field>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Search keywords for portals" hint="Comma-separated. Live sweeps use the first one.">
          <input className={input} value={f.keywords} onChange={set("keywords")} />
        </Field>
        <Field label="HSN codes" hint="Comma-separated"><input className={input} value={f.hsn} onChange={set("hsn")} /></Field>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Annual turnover" hint={turnoverPreview != null ? `= ${inr(turnoverPreview)}` : "e.g. 1.2 Cr or 40 lakh"}>
          <input className={input} value={f.turnover} onChange={set("turnover")} />
        </Field>
        <Field label="Years in business"><input className={input} inputMode="numeric" value={f.years} onChange={set("years")} /></Field>
        <Field label="States you serve" hint="Comma-separated"><input className={input} value={f.states} onChange={set("states")} /></Field>
      </div>
      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-semibold">Certifications and registrations</legend>
        <div className="flex flex-wrap gap-2">
          {CERTS.map((c) => {
            const on = f.certs.includes(c);
            return (
              <button type="button" key={c} aria-pressed={on}
                      onClick={() => setF({ ...f, certs: on ? f.certs.filter((x) => x !== c) : [...f.certs, c] })}
                      className={`rounded-full border px-3 py-1 text-sm ${on ? "border-ink bg-ink text-paper" : "border-line bg-white"}`}>
                {on ? "✓ " : ""}{c}
              </button>
            );
          })}
        </div>
        <input className={input} placeholder="Others, comma-separated (e.g. BIS, ISO 14001)" value={f.otherCerts} onChange={set("otherCerts")} />
      </fieldset>
      {error && <p role="alert" className="rounded-lg bg-bad-bg px-3 py-2 text-bad">{error}</p>}
      <button disabled={busy} className="self-start rounded-lg bg-hawk px-5 py-2.5 font-semibold text-white disabled:opacity-60">
        {busy ? "Saving…" : "Save profile and open dashboard"}
      </button>
    </form>
  );
}
