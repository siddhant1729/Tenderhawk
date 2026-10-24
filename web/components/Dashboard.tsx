"use client";

import Link from "next/link";
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { api, ApiError, sweepEventsUrl } from "@/lib/api";
import { inr } from "@/lib/format";
import { applyEvent, initialSweep, type SweepState } from "@/lib/sweep";
import type { MatchSummary, Profile, StepEvent } from "@/lib/types";
import AgentPanel from "./AgentPanel";
import ErrorBox from "./ErrorBox";
import MatchList from "./MatchList";

type Action = { type: "reset" } | { type: "event"; ev: StepEvent };
const reducer = (s: SweepState, a: Action): SweepState => (a.type === "reset" ? initialSweep() : applyEvent(s, a.ev));

const storeKey = (profileId: number) => `th:sweep:${profileId}`;
const remember = (k: string, v: string | null) => {
  try {
    if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k);
  } catch { /* private mode: the panel just won't resume after a reload */ }
};
const recall = (k: string) => {
  try { return sessionStorage.getItem(k); } catch { return null; }
};

export default function Dashboard({ profileId, followSweepId }: { profileId: number; followSweepId?: string }) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [matches, setMatches] = useState<MatchSummary[] | null>(null);
  const [matchError, setMatchError] = useState<string | null>(null);
  const [sweep, dispatch] = useReducer(reducer, undefined, initialSweep);
  const [starting, setStarting] = useState(false);
  const [lost, setLost] = useState(false);
  const [following, setFollowing] = useState(false);   // a sweep's stream is open (or being opened)
  const [fallback, setFallback] = useState<boolean | null>(null);
  const esRef = useRef<EventSource | null>(null);

  const loadMatches = useCallback(() => {
    api.matches(profileId).then(
      (ms) => { setMatches(ms); setMatchError(null); },
      (e) => setMatchError(e instanceof ApiError ? e.message : "Couldn’t load matches."),
    );
  }, [profileId]);

  const loadProfile = useCallback(() => {
    api.profile(profileId).then(
      (p) => { setProfile(p); setProfileError(null); },
      (e) => setProfileError(e instanceof ApiError && e.status === 404
        ? `There is no profile ${profileId}.` : e instanceof Error ? e.message : "Couldn’t load the profile."),
    );
  }, [profileId]);

  useEffect(() => {
    loadProfile();
    loadMatches();
    api.health().then((h) => setFallback(h.fallback_mode), () => setFallback(null));
  }, [loadProfile, loadMatches]);

  /** Subscribe to a sweep's event stream. Only refs, storage and the EventSource here: state changes
   *  happen in its callbacks, so this is safe to call from an effect (resume) or a click (new sweep). */
  const follow = useCallback((id: string) => {
    esRef.current?.close();
    remember(storeKey(profileId), id);
    const es = new EventSource(sweepEventsUrl(id));
    esRef.current = es;
    es.onopen = () => setFollowing(true);
    es.onmessage = (msg) => {
      let ev: StepEvent;
      try { ev = JSON.parse(msg.data); } catch { return; }
      setLost(false);
      dispatch({ type: "event", ev });
      if (ev.agent !== "all") return;
      // all/done frees the Sweep button; with documents still pending the stream stays open for
      // all/update (a match changed: reload the list) until all/settled.
      const last = ev.type === "settled" || (ev.type === "done" && !ev.pending);
      if (ev.type === "done" || ev.type === "update" || last) loadMatches();
      if (ev.type === "done") setFollowing(false);
      if (last) {
        es.close();
        remember(storeKey(profileId), null);
      }
    };
    // EventSource reconnects by itself and the server replays the log; just tell the user meanwhile.
    es.onerror = () => {
      if (es.readyState !== EventSource.CLOSED) return setLost(true);
      setLost(false);
      setFollowing(false);
      remember(storeKey(profileId), null);   // e.g. the sweep's log expired (404): stop trying to resume it
    };
  }, [profileId, loadMatches]);

  // Resume a sweep that was running before a page reload.
  useEffect(() => {
    const saved = followSweepId || recall(storeKey(profileId));
    if (saved) follow(saved);
    return () => esRef.current?.close();
  }, [profileId, follow, followSweepId]);

  function stopWatching() {
    esRef.current?.close();
    esRef.current = null;
    remember(storeKey(profileId), null);
    setFollowing(false);
    setLost(false);
    dispatch({ type: "event", ev: { sweep_id: "", agent: "all", type: "done", step: 0,
      message: "Stopped watching this sweep. Results it finishes will still appear under Matches.",
      count: null, direct_url: null, ts: "" } });
  }

  async function startSweep() {
    setStarting(true);
    try {
      const { sweep_id } = await api.startSweep(profileId);
      dispatch({ type: "reset" });
      setLost(false);
      setFollowing(true);   // keep the button disabled until the stream ends: no double-billed sweeps
      follow(sweep_id);
    } catch (e) {
      dispatch({ type: "reset" });
      dispatch({ type: "event", ev: { sweep_id: "", agent: "all", type: "error", step: 0,
        message: e instanceof Error ? e.message : "Couldn’t start the sweep.", count: null, direct_url: null, ts: "" } });
    } finally {
      setStarting(false);
    }
  }

  if (profileError) {
    return (
      <div className="flex flex-col gap-3">
        <ErrorBox message={profileError} onRetry={loadProfile} />
        <Link href="/" className="text-sm underline">Create a profile</Link>
      </div>
    );
  }

  const running = following && !sweep.finished;
  const keywords = profile ? (profile.keywords.length ? profile.keywords : profile.products) : [];
  const searched = fallback === false ? keywords.slice(0, 1) : keywords;
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3 rounded-2xl bg-ink p-5 text-paper md:flex-row md:items-center md:justify-between">
        {profile ? (
          <div className="flex flex-col gap-1">
            <h1 className="text-2xl font-bold">{profile.name}</h1>
            <p className="text-sm text-paper/80">
              Turnover {inr(profile.annual_turnover)} · {profile.years_in_business ?? "—"} years ·{" "}
              {profile.certifications.join(", ") || "no certifications listed"} · serves{" "}
              {profile.states_served.join(", ") || "—"}
            </p>
            <p className="text-sm text-paper/80">
              Searching for: <b>{searched.join(", ")}</b>
              {fallback === false && keywords.length > 1 && <> (live sweeps use the first keyword)</>}
            </p>
          </div>
        ) : (
          <p className="text-paper/80">Loading profile…</p>
        )}
        <button onClick={startSweep} disabled={!profile || starting || (running && !lost)}
                className="rounded-xl bg-hawk px-6 py-3 text-lg font-bold text-white disabled:opacity-60">
          {running ? "Sweeping…" : starting ? "Starting…" : "Sweep now"}
        </button>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-bold">Live agents</h2>
        {lost && <p role="status" className="text-sm text-warn">Lost the live connection. Reconnecting…</p>}
        {running && (
          <button onClick={stopWatching} className="self-start text-sm text-ink-soft underline">
            Stop watching this sweep
          </button>
        )}
        <AgentPanel state={sweep} />
      </section>

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">Matches</h2>
          <button onClick={loadMatches} className="text-sm underline">Refresh</button>
        </div>
        {matchError ? <ErrorBox message={matchError} onRetry={loadMatches} />
          : matches ? <MatchList matches={matches} />
          : <p className="text-ink-soft">Loading matches…</p>}
      </section>
    </div>
  );
}
