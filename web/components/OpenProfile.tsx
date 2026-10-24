"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

export default function OpenProfile() {
  const router = useRouter();
  const [id, setId] = useState("1");
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (/^\d+$/.test(id)) router.push(`/profiles/${id}`); }}
          className="flex items-end gap-2 rounded-2xl border border-line bg-card p-4">
      <label className="flex flex-col gap-1">
        <span className="text-sm font-semibold">Open an existing profile</span>
        <input aria-label="Profile id" value={id} onChange={(e) => setId(e.target.value.trim())} inputMode="numeric"
               className="w-28 rounded-lg border border-line bg-white px-3 py-2" />
      </label>
      <button className="rounded-lg bg-ink px-4 py-2 font-semibold text-paper">Open</button>
      <span className="pb-2 text-xs text-ink-soft">The seeded demo profile prints its id: <code>python -m delivery.seed</code></span>
    </form>
  );
}
