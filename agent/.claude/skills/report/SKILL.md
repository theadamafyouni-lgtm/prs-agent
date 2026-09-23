---
name: report
description: Stage 4. One structured output — the score, where the person stands, the honest limits personalized to them, and a disclaimer — delivered with care. Handles the follow-up state and the explain-my-result vs tell-me-what-to-do-medically boundary (REP-1..REP-14). Blocked until INT-1 exists.
---

# report

**This is the hardest stage, not the easy one.** The spec says so explicitly and says it must not
be treated as simple. Saying *"here's a useful idea, and here's why it might mean little for you"*
clearly, to someone who knows nothing about genetics, is the single hardest thing this product
does. That sentence is the design target (`REP-5`).

Blocked until `INT-1` lands, because there is no placement to report. Blocked does not mean
unwritten: everything below is the behaviour, and the follow-up state is exercisable now.

## The framing, before any wording

- **`FRAME-1`** The person knows nothing about genetics. That is the fair default, not an insult.
- **`FRAME-2`** Lead with **a useful idea of where you stand, with honest limits.**
- **`FRAME-4`** The limits are carried **alongside** the result, not led with. *"Your genome barely
  says anything"* must never be the headline. The weighting is a mix, tilted toward the useful idea.
- **`REP-2`** Narrow means scoped to *this result* — not a genomics lecture. **Narrow must never
  mean skipping the explanation.**

## What the report contains (`REP-1`)

One structured output:

1. **The score**, and what kind of thing it is.
2. **Where the person stands** against the population.
3. **A disclaimer** that it is not a certainty and not a diagnosis.
4. **The covered-weight fraction** — how much of the score's weight was actually resolvable — as a
   **transparency control, not a correction applied to the number.** Do not adjust the score by it;
   show it. The quantity is `resolved_weight_fraction`, a top-level key of
   `runs/<RUN_ID>/score/coverage_<PGS>.json` (written by `tools/weight_coverage.py measure`). Read
   that **measured** file, not `select`'s `coverage_est_<PGS>.json` — estimate mode emits the same
   key name for a pre-imputation, typed-only quantity, so pulling the wrong file understates
   coverage to the person.

## A percentile is not a risk, and it does not sit still (`REP-3`)

Two things to get across, both non-obvious to a lay reader:

- **90th percentile is not "90% chance I get it."** It says where this person sits relative to
  others, not how likely the outcome is. Never dump a raw percentile and move on.
- **The percentile itself moves.** The same person can land on a different number depending on how
  the score was computed and placed. It is an estimate, not a fixed coordinate. If a stochastic
  imputation pipeline had been used, that alone could shift an individual by more than 10
  percentile points with no change to the genetic model at all — which is why this pipeline is
  deterministic and why the number still deserves hedging.

## The honest limit, personalized (`REP-4`)

Not a generic caveat. **The concrete published figure for this person's actual array and
ancestry.** Cross-ancestry portability is the dominant term: a score trained in one ancestry loses
substantial, quantified accuracy when read on a genome from another (Martin 2019), and this is
exactly the population `FRAME-3` says the product serves worst — the people who would most want an
answer are the ones it is least valid for. State that concretely for their case rather than
gesturing at it.

Pull from the run, do not invent: the model's development ancestry, its evaluation ancestry and
the actual reported metrics, the coverage fraction, the assay-match status, and the scoring-parity
warning if `INT-4` or `R1` fired.

## Delivering a hard result with care (`REP-6`, `REP-7`)

**Deliver it, do not just state it.** A high number landing on a lay person alone, at midnight,
with no support, is a real harm — which is why genetic results are normally delivered by a trained
counselor. Tone, framing and pointing to support are **core agent behavior, not backend.**

In practice: say the number plainly without drama; say immediately what it does and does not mean;
do not stack alarming framings; make the next step concrete and low-stakes; and leave the person
somewhere to go rather than alone with a number.

## When the stage refuses instead

A refusal still gets delivered with care, and it still has content. If `select` refused below the
coverage floor, or `interpret` could not build a usable reference, the person is owed:

- what was actually tried, in plain language;
- **why refusing is the honest answer here** — not a system failure, and not their fault;
- specifically that a refusal driven by ancestry-related coverage is a statement about the
  available science and reference data, **not** about them;
- what would change the answer (a different assay, a better-matched model, a reference that
  includes more people like them);
- the same clinician handoff if they have a medical question.

**Those same four items also go into `report.json` as `refusal_envelope`, written TO the person.**
The window renders the refusal screen from that block directly, so it is the delivery, not a
record of one. `delivered` describes what you said; `refusal_envelope` *is* what you say. Write it
in the register you would use speaking to them -- second person, present tense, no `REP-` ids, no
"told them that". "Named specifically: the file was read and verified as genuine" is a note.
"Your file is genuine and in good order" is the envelope.

`headline` is one sentence, the thing they need first, and it is not an apology for the product
working. `is_about_the_person` is `false` whenever the refusal is driven by coverage, reference
data or the reach of their assay -- the window then says so on your behalf, and that is the
sentence that stops someone reading a limitation of the data as a fact about their body.

## The follow-up state — report is not a dead end (`REP-8`..`REP-14`)

After the report the person can **exit**, or **ask for more detail**. The agent can **fetch more**
(`REP-9`) but keeps it relevant to this patient and this result — not open-ended genomics trivia.
Same core applies: refuse when it should not answer (`REP-10`).

**The boundary that has to be drawn deliberately (`REP-14`).** It blurs live, and the same number
sits on both sides:

| The agent answers, and does it well (`REP-11`) | The agent hands off (`REP-12`) |
|---|---|
| "What does 90th percentile mean?" | "What does 90th percentile mean **for my chances of getting this**?" |
| What a PRS is, what the number means | "Should I start medication?" |
| Why it is a weak signal, what the limits are | "Should I get screened?" |
| Why coverage or ancestry matters here | "What do I do about this?" / "Will I get it?" |

Same number, different question. Make the distinction **on purpose**, do not treat the two as the
same, and do not let a medical question through because it was phrased as a curiosity.

**The handoff is warm, not a cold refusal (`REP-13`).** "I can't advise on that" is not enough.
Offer to help find a clinician, suggest making an appointment, help with the contact step. The
person came with a real concern; hand them to someone who can act on it rather than closing a door.

## Record

```bash
python3 tools/record.py --run <RUN_ID> --stage report --name report --judgment - <<'JSON'
{
  "decision": "...", "outcome": "score|refuse",
  "requirement_ids": ["REP-1", "REP-3", "REP-4"],
  "delivered": {"score": null, "placement": null, "resolved_weight_fraction": 0.0,
                "personalized_limit": "...", "disclaimer": "..."},
  "refusal_envelope": {"headline": "...", "detail": "...",
                       "what_was_tried": "...", "why_this_is_honest": "...",
                       "what_would_change_it": "...", "is_about_the_person": false},
  "follow_up_state": "open",
  "reasons": ["..."],
  "evidence": [{"source": "runs/<RUN_ID>/interpret/...", "field": "..."}]
}
JSON
```
