"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, NATIVE, LANG_NAME } from "@/lib/api";

const TARGETS = ["ta-IN", "te-IN", "bn-IN", "mr-IN", "en-US", "ar-SA", "pt-BR", "es-MX"];

const STEPS = [
  { t: "Transcribe", d: "Whisper turns the Hindi episode into timed lines. A script upload skips this step." },
  { t: "Build the character bible", d: "Every speaker gets a gender, age, persona and a name spelled in each target script." },
  { t: "Adapt each line", d: "The localization agent replaces idioms, kinship terms and units, and keeps each line inside its time slot." },
  { t: "Check before anyone listens", d: "Rules catch missing lines, wrong names, changed numbers, literal idioms and overlong lines. An LLM judge reviews meaning, tone and formality. Flagged lines go back for a rewrite." },
  { t: "Voice and mix", d: "Each character keeps one voice per language. Lines are fitted to the original timing and mixed into the episode." },
  { t: "Review what's flagged", d: "A reviewer hears the original and the dub side by side, fixes a line, and only that line is re-voiced." },
];

interface Bench {
  rules?: { results: { lang: string; recall_total: number; false_positive_rate: number; recall_excluding_semantic: number }[] };
  judge?: { results: { lang: string; recall_total: number; false_positive_rate: number }[] };
}

export default function Home() {
  const [bench, setBench] = useState<Bench | null>(null);
  useEffect(() => {
    api<Bench>("/api/benchmark").then(setBench).catch(() => setBench(null));
  }, []);
  const rules = bench?.rules?.results?.[0];
  const judge = bench?.judge?.results?.[0];

  return (
    <>
      <section className="grid grid-cols-1 gap-12 pt-16 pb-20 md:grid-cols-[1fr_16rem] md:pt-24">
        <div>
          <p lang="hi" className="display reveal text-hero text-ink break-words">
            इस लड़की ने हमारी इज़्ज़त मिट्टी में मिला दी!
          </p>
          <div className="reveal-late mt-8 space-y-3 measure">
            <p className="text-lg text-muted">
              <span className="struck">This girl has mixed our honour into the soil!</span>
              <span className="ml-3 text-sm text-bad">word for word</span>
            </p>
            <p className="text-xl text-ink">
              This girl has dragged our family&apos;s name through the mud!
              <span className="ml-3 align-middle text-sm text-ok">what an American viewer should hear</span>
            </p>
          </div>
          <p className="mt-10 measure text-base text-muted">
            GlobalDub turns one Hindi audio-drama episode into dubbed episodes in eight languages. It adapts idioms instead
            of translating them, keeps every character&apos;s voice consistent, fits each line to the original scene, and
            flags its own mistakes so a reviewer only checks the lines that need a human.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/demo" className="rounded-md bg-brand px-5 py-2.5 font-medium text-white hover:bg-ink">
              Hear the demo episode
            </Link>
            <Link href="/new" className="rounded-md border border-ink/20 px-5 py-2.5 font-medium text-ink hover:border-ink">
              Dub your own script
            </Link>
          </div>
        </div>

        <aside aria-label="Supported languages" className="md:border-l md:border-line md:pl-8">
          <p className="mb-3 text-sm text-muted">From Hindi into</p>
          <ul className="space-y-2">
            {TARGETS.map((c) => (
              <li key={c} className="flex items-baseline justify-between gap-3">
                <span className="text-xl" dir={c === "ar-SA" ? "rtl" : undefined} lang={c.slice(0, 2)}>
                  {NATIVE[c]}
                </span>
                <span className="whitespace-nowrap text-xs text-muted">{LANG_NAME[c]}</span>
              </li>
            ))}
          </ul>
        </aside>
      </section>

      <section className="border-t border-line py-16">
        <h2 className="display text-xl">How an episode moves through the system</h2>
        <ol className="mt-8 grid gap-x-10 gap-y-8 md:grid-cols-2">
          {STEPS.map((s, i) => (
            <li key={s.t} className="grid grid-cols-[2rem_1fr] gap-2">
              <span className="display text-lg text-brand">{i + 1}</span>
              <div>
                <h3 className="font-semibold">{s.t}</h3>
                <p className="mt-1 text-muted">{s.d}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="border-t border-line py-16">
        <h2 className="display text-xl">How well the QA agent catches errors</h2>
        <p className="mt-3 measure text-muted">
          We take clean dubbed lines, plant one known error in each copy (a dropped clause, a swapped name, a changed
          amount, a literal idiom, untranslated text, a line too long for its scene, a reversed meaning) and count how
          many the QA agent flags.
        </p>
        <dl className="mt-8 grid gap-8 sm:grid-cols-3">
          <div>
            <dt className="text-sm text-muted">Factual errors caught by rules alone</dt>
            <dd className="display text-2xl">{rules ? `${Math.round(rules.recall_excluding_semantic * 100)}%` : "-"}</dd>
          </div>
          {judge && (
            <div>
              <dt className="text-sm text-muted">All planted errors caught, rules + LLM judge</dt>
              <dd className="display text-2xl">{Math.round(judge.recall_total * 100)}%</dd>
            </div>
          )}
          <div>
            <dt className="text-sm text-muted">Clean lines wrongly flagged</dt>
            <dd className="display text-2xl">
              {judge ? `${Math.round(judge.false_positive_rate * 100)}%` : rules ? `${Math.round(rules.false_positive_rate * 100)}%` : "-"}
            </dd>
          </div>
        </dl>
        <Link href="/benchmark" className="mt-6 inline-block text-brand underline">
          See the full benchmark by error type
        </Link>
      </section>
    </>
  );
}
