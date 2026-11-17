# Model Card: PawPal+ AI Care Advisor

## Overview

| | |
|---|---|
| **Feature** | AI Care Advisor: suggests pet-care tasks for one pet, grounded in local guidelines (RAG) |
| **Model** | Google Gemini `gemini-3.1-flash-lite` (fallback `gemini-3.6-flash`), via the `google-genai` SDK, temperature 0.2, JSON-schema output |
| **Retrieval** | Keyword scoring over 20 passages in `knowledge/` (6 markdown files), top 4 per request |
| **Inputs** | Pet species and age, owner care notes, the pet's existing task titles, retrieved passages |
| **Outputs** | Up to 5 task suggestions (title, duration, priority, frequency, optional time, source id, rationale), each checked by guardrails and reviewed by the owner |
| **Intended use** | Helping a pet owner think of routine care tasks (walks, feeding, litter, grooming, enrichment) and fit them into a daily plan |
| **Not intended for** | Medical diagnosis, medication dosing, emergencies, or replacing a veterinarian |

## Limitations and biases

- **Small, hand-written knowledge base.** 20 general passages written for this project, covering dogs, cats and general care. Exotic pets ("other" species) only get general passages. The guidance reflects mainstream Western pet-care norms and is not veterinary-reviewed.
- **Coarse pet profile.** Age is bucketed into young (<1), adult and senior (8+), and breed, size, weight and health conditions are ignored. A 7-year-old giant breed is treated as "adult" even though it may be senior.
- **Exact-word retrieval.** Retrieval only matches words that appear in a passage, so synonyms ("arthritis" vs "joint stiffness") or misspellings in the care notes are missed. Passages that repeat the species word are favoured.
- **The AI can't see the day.** Gemini suggests times from the guidelines without knowing the owner's schedule, so it can propose clashing times. In evaluation case 1 it put both meals at the same times as the walks; the scheduler skipped them as conflicts.
- **Guardrails check form, not truth.** They verify that a suggestion is well-formed and cites a retrieved passage, but not that the task actually matches what the passage says. A plausible but wrong reading of a real passage would pass.
- **Model availability.** Results depend on Google's hosted models. During testing, newer models returned 503 (overloaded) and older ones 404 (retired); model names must be updated over time.
- **Small evaluation.** 3 live cases and one repeat run are enough to show the system works, not to measure accuracy across many pets.

## Future improvements

- **Let the AI see the schedule.** Pass the pet's existing fixed times into the prompt, or check suggested times against the scheduler before showing them, so suggestions like the clashing meal times in case 1 get caught or moved earlier.
- **Better retrieval.** Add synonyms or switch to embeddings so care notes like "arthritis" or misspellings still find the right passage.
- **Richer pet profile.** Use breed and size, so a 7-year-old giant breed counts as senior.
- **Bigger, reviewed knowledge base.** More species and topics, checked by a veterinarian.
- **Check meaning, not just format.** Add a second check that the task really matches what its cited passage says (for example, a duration that matches the guideline).
- **Larger evaluation.** Run many more pet profiles and track the pass rate over time instead of 3 cases.

## Misuse and prevention

| Possible misuse | How the system limits it |
|---|---|
| Treating suggestions as veterinary or medication advice | Every knowledge file says "General guidance only, not veterinary advice." Medication passages cover routine (consistent times, finishing courses) rather than dosing. Suggestions are only care tasks, never diagnoses. |
| Prompt injection through the care-notes field (e.g. "ignore the guidelines") | Notes only influence retrieval and the prompt. Output must fit the JSON schema, every task must cite a passage that was actually retrieved, and durations and times are range-checked, so injected instructions can't produce free text or invented sources. The owner still reviews everything. |
| Blindly adding AI tasks | Nothing is added automatically. The owner ticks which suggestions to keep and can see the rationale and source guideline for each. |
| Leaking the API key | The key lives in `.env`, which is gitignored; `.env.example` holds a placeholder. The key is never logged or shown in the UI. |
| Flooding the API | One request per button click, at most 5 suggestions, 30 s timeout and a capped number of retries. |

Further steps for a real deployment: have a veterinarian review the knowledge base, add a visible disclaimer in the UI, and rate-limit requests per user.

## Testing results

- **Unit tests:** 49/49 pass (21 for the scheduler, 28 for the advisor). They use a fake LLM, so they need no API key: retrieval, every guardrail, retry and fallback, error handling, and accepted suggestions flowing into the daily plan.
- **Live evaluation (`evaluate.py`):** 25/25 checks passed. 3 real pets (adult dog, senior cat, puppy) produced 13 suggestions; all 13 cited a retrieved guideline and none duplicated an existing task. A repeat run cited the same guidelines (100% overlap).
- **Guardrails:** all 6 bad suggestions in the scripted test were rejected for the right reason, and a non-JSON reply and a simulated outage both produced a friendly error instead of a crash.
- **Before vs after fixes:** the first live run rejected 5 of 5 valid suggestions (bracketed source ids); after the fix, 0 valid suggestions were wrongly rejected. One early call hung for over 90 seconds; every call now has a 30 s limit, retries, a fallback model, and a clear error if all of them fail (the fallback path is covered by unit tests, but it never had to kick in during live runs).
- **Known miss:** in case 1, 2 of 5 suggestions (the meals) were skipped by the scheduler because Gemini timed them at the same moment as the walks.

## What surprised me while testing reliability

- **The fake-LLM tests passed but the real model failed.** All unit tests passed, yet the first live run rejected 5 out of 5 good suggestions. Gemini wrote sources as `[dog.md#exercise-and-walks]`, probably copying how the prompt lists guidelines (`[id] heading`), so the "cites a retrieved source" guardrail rejected all of them. Only a live run could reveal this. The fix strips the brackets and still rejects made-up sources.
- **Model availability was the biggest risk.** The newest models were overloaded (503) and the older 2.5 models had been retired (404). One call hung for over 90 seconds. Timeouts, retries and a fallback model turned out to matter more than prompt wording.
- **The AI and the scheduler disagreed.** Gemini's suggested meal times were sensible on their own but clashed with its own walk times. The existing conflict check caught it, which showed the value of keeping the rule-based scheduler as the final authority.
- **Consistency was better than expected.** At temperature 0.2, repeating the same request cited exactly the same three guidelines.

## AI collaboration

I used an AI coding assistant for most of this project. I'd tell it what I wanted, it would read the code and suggest a plan, and I'd critique the plan and implementation, and then decide what to go ahead with. It wrote a lot of the code and tests and ran them. I picked the feature and the AI provider, reviewed what changed, and committed the work one step at a time so I could keep track of it. I made sure to test edge cases to ensure they were accounted for.

**A suggestion I accepted:** The first time I tried the advisor with real Gemini, the model was overloaded and kept returning errors. One call also froze for over a minute and a half. The AI suggested a time limit on each call, a couple of retries, and a backup model if the main one still failed. I said yes because without it the app just didn't work whenever Google was busy, and the change was small and could be tested without calling the real API.

**A suggestion I changed:** When I asked which feature to build, the AI suggested the RAG advisor plus a separate evaluation script, and it set the app up to use the newest Gemini model. I cut it back to just the advisor so I could get one thing working first, and I told it the model didn't have to be the newest. That turned out to be crucial because the newest models were overloaded, and the lighter one we switched to answered right away. We did add an evaluation script later, but only once the advisor worked and I needed real output to show in the README.
