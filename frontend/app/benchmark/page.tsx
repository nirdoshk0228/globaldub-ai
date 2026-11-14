"use client";

import { useEffect, useState } from "react";
import { api, LANG_NAME } from "@/lib/api";
import { ErrorNote } from "@/components/ui";

interface TypeStat { planted: number; detected: number; diagnosed: number; recall: number | null }
interface Result {
  lang: string;
  judge: boolean;
  llm: string;
  clean_lines: number;
  false_positive_rate: number;
  recall_total: number;
  recall_excluding_semantic: number;
  by_type: Record<string, TypeStat>;
}
interface Bench { rules?: { generated_at: number; results: Result[] }; judge?: { generated_at: number; results: Result[] } }

const TYPE_LABEL: Record<string, string> = {
  omission_full: "Whole line dropped",
  omission_partial: "Clause dropped",
  name_swap: "Character name swapped",
  number_change: "Amount or count changed",
  untranslated: "Left in Hindi",
  literal_idiom: "Idiom translated literally",
  duration_bloat: "Too long for the scene",
  meaning_flip: "Meaning reversed",
};

function Table({ title, note, results }: { title: string; note: string; results: Result[] }) {
  const types = Object.keys(results[0]?.by_type ?? {});
  return (
    <section className="mt-12">
      <h2 className="display text-xl">{title}</h2>
      <p className="mt-1 measure text-sm text-muted">{note}</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[32rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink/30 text-left text-muted">
              <th className="py-2 pr-4 font-normal">Planted error</th>
              {results.map((r) => <th key={r.lang} className="py-2 pr-4 text-right font-normal">{LANG_NAME[r.lang] ?? r.lang}</th>)}
            </tr>
          </thead>
          <tbody>
            {types.map((t) => (
              <tr key={t} className="border-b border-line">
                <td className="py-2.5 pr-4">{TYPE_LABEL[t] ?? t}</td>
                {results.map((r) => {
                  const v = r.by_type[t];
                  return (
                    <td key={r.lang} className="py-2.5 pr-4 text-right tabular-nums">
                      {v.planted ? `${v.detected} of ${v.planted}` : "-"}
                    </td>
                  );
                })}
              </tr>
            ))}
            <tr className="border-b border-line font-medium">
              <td className="py-2.5 pr-4">Caught overall</td>
              {results.map((r) => <td key={r.lang} className="py-2.5 pr-4 text-right tabular-nums">{Math.round(r.recall_total * 100)}%</td>)}
            </tr>
            <tr className="font-medium">
              <td className="py-2.5 pr-4">Clean lines wrongly flagged</td>
              {results.map((r) => <td key={r.lang} className="py-2.5 pr-4 text-right tabular-nums">{Math.round(r.false_positive_rate * 100)}%</td>)}
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Benchmark() {
  const [b, setB] = useState<Bench | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api<Bench>("/api/benchmark").then(setB).catch((e) => setError(e.message));
  }, []);

  return (
    <div className="pt-12">
      <h1 className="display text-2xl">QA benchmark</h1>
      <p className="mt-2 measure text-muted">
        Each clean dubbed line is copied once per error type, one known error is planted in the copy, and the QA agent
        runs on every copy. A planted error counts as caught when QA raises a medium or high flag on that line. The
        same clean lines are also checked on their own, to measure how often QA flags a line that is actually fine.
      </p>
      {error && <div className="mt-6"><ErrorNote>{error}</ErrorNote></div>}
      {b?.rules && (
        <Table title="Rules only" note="Deterministic checks: free, instant and explainable. They cannot see meaning, so reversed meanings get through." results={b.rules.results} />
      )}
      {b?.judge && (
        <Table title="Rules plus LLM judge" note={`The full QA agent, with the ${b.judge.results[0]?.llm ?? "LLM"} judge reviewing meaning, tone and formality.`} results={b.judge.results} />
      )}
      {b && !b.rules && !b.judge && <p className="mt-8 text-muted">No benchmark has been run on this server yet.</p>}
      <p className="mt-10 text-sm text-muted">
        Reproduce: <code>python eval/planted_errors.py --langs en-US --judge</code> in the backend folder.
      </p>
    </div>
  );
}
