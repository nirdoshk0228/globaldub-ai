# Interview notes

## 30-second pitch

Kuku's next growth is taking Indian stories to new audiences, in other Indian languages and abroad. Translation is
the easy part: idioms, names, amounts, timing and character voices are where dubs break, and a human checking every
line in every language doesn't scale. I built GlobalDub: one Hindi episode in, eight dubbed languages out. It adapts
idioms instead of translating them, fits every line to the original scene, and its QA agent catches X% of planted
errors, so a reviewer only checks Z% of lines. It's deployed, with a review studio where fixing a line re-voices only
that line, at $N per finished minute.

(Fill in X, Z and N from your own benchmark and report pages. Never quote a number you haven't measured.)

## Resume bullets (template)

**GlobalDub AI: Multilingual Audio Dubbing & Localization QA Platform** | LangGraph, Gemini, Whisper, FastAPI, Next.js, Postgres, Redis | [live demo] [GitHub]

- Built and deployed an agentic pipeline (LangGraph) that dubs Hindi audio drama into 8 languages (4 Indian, 4 international) with idiom adaptation, pinned character voices and timing fitted to the original scene; new markets are added through YAML language packs, with no code changes.
- Designed a hybrid QA agent (language-aware rules + LLM judge + audio duration check) with a self-correction loop; it caught **X%** of planted localization errors at a **Y%** false-positive rate.
- Shipped a production web app (Next.js, FastAPI, Redis/RQ workers, Postgres, R2) with a review studio that re-voices only edited lines, lost-update-safe concurrent writes, and cost guardrails; **$N per finished minute**, with **Z%** of lines needing human review.

## Questions to expect

**Why not just use ElevenLabs Dubbing or another dubbing API?**
Those voice the translation. They don't tell you *which* lines are wrong. The QA layer and the review workflow are
the parts a content company actually pays editors for, so that's where this project spends its effort.

**Why rules and an LLM judge? Why not only the LLM?**
Rules are free, instant, deterministic and explainable, and they handle factual errors best (names, numbers,
missing lines, timing). The LLM judge sees meaning and tone. The benchmark shows the split: rules caught 0 of 6
reversed meanings, which is exactly the gap the judge exists to fill.

**How do you know the QA works?**
With a planted-error benchmark: clean lines, one known error per copy, then measure recall per error type and the
false-positive rate on clean lines. It's reproducible with one command and runs in CI.

**How does "50 lakh" vs "5 million" work?**
Numbers are parsed into values in every script, multipliers included, then compared as counted multisets. A unit
conversion passes; "50 million" for "5 million" fails.

**What happens when two people edit at once, or a reviewer edits while the worker is still running?**
Every write is a locked read-merge-write (a Redis lock shared by the API and worker processes), and each writer
replaces only what it owns: the worker writes the tracks it's processing, and a re-render writes one line, only if
that line's version hasn't changed since.

**Why a queue?**
Dubbing takes minutes. The API stays fast, a job survives an API restart, and RQ retries failed jobs.

**What would you do next at Kuku?**
Train the language packs on Kuku's own glossary and reviewer edits (every edit is logged as before → after with the
QA flags it fixed: free training data); add lip-sync for Kuku TV video; clone original voices with actor consent;
and A/B-test adapted vs literal dubs on completion rate.

**What about background music?**
Like a studio, the dub sits on an M&E (music & effects) track. If the show has one, upload it; otherwise Demucs
separates voices from the mix. New lines are matched to the original dialogue loudness and the music ducks
under speech. On the free server Demucs doesn't fit in 512 MB, so heavy separation runs locally and the server
only mixes - a cost trade-off I'd remove with one GPU worker in production.

**What happens with songs, and how do you pick a male or female voice from audio?**
Pitch tracking: men speak around 85-165 Hz and women around 165-255 Hz, so each speaker's median pitch sets the voice;
two clearly separated pitch clusters become two speakers. Singing holds notes while speech glides, so lines with long
held notes are flagged as sung and kept in the original voice. TTS reading lyrics sounds wrong, and real dubs keep songs
or re-record them with a singer. I found this when I tested a song upload and got a female voice reading a male
singer's lyrics.

**Weak points?**
No lip-sync; diarization only runs locally; Edge-TTS is an unofficial API, which is why there's a fallback provider.
