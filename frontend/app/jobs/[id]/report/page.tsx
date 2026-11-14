"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, CHECK_LABEL, fmtTime, money, TrackSummary } from "@/lib/api";
import { ErrorNote, LangName } from "@/components/ui";

interface Report {
  job_id: string;
  title: string;
  runtime_seconds: number;
  total_cost_usd: number;
  ingest: { stage: string; seconds: number; cost_usd: number }[];
  tracks: (TrackSummary & { metrics: { stage: string; seconds: number; cost_usd: number; units: Record<string, number> }[] })[];
  review: Record<string, { approve: number; edit: number; reopen: number; seconds: number }>;
}

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const [r, setR] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Report>(`/api/jobs/${id}/report`).then(setR).catch((e) => setError(e.message));
  }, [id]);

  if (error) return <div className="pt-12"><ErrorNote>{error}</ErrorNote></div>;
  if (!r) return <p className="pt-12 text-muted">Loading...</p>;
  const checks = [...new Set(r.tracks.flatMap((t) => Object.keys(t.flags_by_check)))];

  return (
    <div className="pt-12">
      <Link href={`/jobs/${id}`} className="text-sm text-muted hover:text-ink">{r.title}</Link>
      <h1 className="display text-2xl">Cost and quality report</h1>
      <p className="mt-2 text-muted">
        {fmtTime(r.runtime_seconds)} episode, total processing cost {money(r.total_cost_usd)}.
      </p>

      <div className="mt-8 overflow-x-auto">
        <table className="w-full min-w-[44rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink/30 text-left text-muted">
              <th className="py-2 pr-4 font-normal">Language</th>
              <th className="py-2 pr-4 text-right font-normal">Passed QA</th>
              <th className="py-2 pr-4 text-right font-normal">Needed review</th>
              <th className="py-2 pr-4 text-right font-normal">Sped up to fit</th>
              <th className="py-2 pr-4 text-right font-normal">Reviewer edits</th>
              <th className="py-2 pr-4 text-right font-normal">Review time</th>
              <th className="py-2 pr-4 text-right font-normal">Processing</th>
              <th className="py-2 text-right font-normal">Cost per minute</th>
            </tr>
          </thead>
          <tbody>
            {r.tracks.map((t) => {
              const rv = r.review[t.lang];
              return (
                <tr key={t.lang} className="border-b border-line">
                  <td className="py-3 pr-4"><LangName code={t.lang} /></td>
                  <td className="py-3 pr-4 text-right tabular-nums">{t.auto_ok}/{t.lines}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{t.needs_review_pct}%</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{t.stretched_lines}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{rv?.edit ?? 0}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{rv ? `${Math.round(rv.seconds)}s` : "-"}</td>
                  <td className="py-3 pr-4 text-right tabular-nums">{Math.round(t.processing_seconds)}s</td>
                  <td className="py-3 text-right tabular-nums">{money(t.cost_per_minute_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {checks.length > 0 && (
        <>
          <h2 className="display mt-12 text-xl">What the QA agent flagged</h2>
          <div className="mt-4 overflow-x-auto">
            <table className="w-full min-w-[36rem] border-collapse text-sm">
              <thead>
                <tr className="border-b border-ink/30 text-left text-muted">
                  <th className="py-2 pr-4 font-normal">Problem</th>
                  {r.tracks.map((t) => <th key={t.lang} className="py-2 pr-4 text-right font-normal">{t.lang}</th>)}
                </tr>
              </thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={c} className="border-b border-line">
                    <td className="py-2.5 pr-4">{CHECK_LABEL[c] ?? c}</td>
                    {r.tracks.map((t) => <td key={t.lang} className="py-2.5 pr-4 text-right tabular-nums">{t.flags_by_check[c] ?? 0}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2 className="display mt-12 text-xl">Where time and money went</h2>
      <div className="mt-4 grid gap-6 md:grid-cols-2">
        {r.tracks.map((t) => (
          <div key={t.lang}>
            <p className="font-medium"><LangName code={t.lang} /></p>
            <ul className="mt-2 text-sm">
              {t.metrics.map((m) => (
                <li key={m.stage} className="flex justify-between border-b border-line py-1.5">
                  <span>{m.stage}</span>
                  <span className="tabular-nums text-muted">{m.seconds.toFixed(1)}s, {money(m.cost_usd)}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
