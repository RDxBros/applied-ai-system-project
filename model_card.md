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

## Misuse and prevention

| Possible misuse | How the system limits it |
|---|---|
| Treating suggestions as veterinary or medication advice | Every knowledge file says "General guidance only, not veterinary advice." Medication passages cover routine (consistent times, finishing courses) rather than dosing. Suggestions are only care tasks, never diagnoses. |
| Prompt injection through the care-notes field (e.g. "ignore the guidelines") | Notes only influence retrieval and the prompt. Output must fit the JSON schema, every task must cite a passage that was actually retrieved, and durations and times are range-checked, so injected instructions can't produce free text or invented sources. The owner still reviews everything. |
| Blindly adding AI tasks | Nothing is added automatically. The owner ticks which suggestions to keep and can see the rationale and source guideline for each. |
| Leaking the API key | The key lives in `.env`, which is gitignored; `.env.example` holds a placeholder. The key is never logged or shown in the UI. |
| Flooding the API | One request per button click, at most 5 suggestions, 30 s timeout and a capped number of retries. |

Further steps for a real deployment: have a veterinarian review the knowledge base, add a visible disclaimer in the UI, and rate-limit requests per user.

## What surprised me while testing reliability

- **The fake-LLM tests passed but the real model failed.** All unit tests passed, yet the first live run rejected 5 out of 5 good suggestions. Gemini wrote sources as `[dog.md#exercise-and-walks]`, probably copying how the prompt lists guidelines (`[id] heading`), so the "cites a retrieved source" guardrail rejected all of them. Only a live run could reveal this. The fix strips the brackets and still rejects made-up sources.
- **Model availability was the biggest risk.** The newest models were overloaded (503) and the older 2.5 models had been retired (404). One call hung for over 90 seconds. Timeouts, retries and a fallback model turned out to matter more than prompt wording.
- **The AI and the scheduler disagreed.** Gemini's suggested meal times were sensible on their own but clashed with its own walk times. The existing conflict check caught it, which showed the value of keeping the rule-based scheduler as the final authority.
- **Consistency was better than expected.** At temperature 0.2, repeating the same request cited exactly the same three guidelines.

## AI collaboration

<!-- Write this section in your own words. The notes below are facts from this project you can draw on; replace them with your answer. -->

**How I used AI during this project:**

<!-- e.g. used an AI coding agent to compare feature options against the brief, plan the RAG design, implement ai_advisor.py and its tests, run the live Gemini tests, and draft documentation; I made the scoping decisions and reviewed results. -->

**One suggestion I accepted:**

<!-- Candidate: after the live 503 errors and a 90-second hang, the AI suggested adding a timeout, retries with backoff, and a fallback model. You accepted it because the app was unusable when Google was overloaded, and the fix was small and testable with a fake client. -->

**One suggestion I did not accept as written (and why):**

<!-- Candidates:
- The AI offered "RAG advisor plus an evaluation script" and other options (agentic workflow, fine-tuning); you scoped it down to the RAG advisor first because it was the best balance of effort and usefulness (fine-tuning was overkill, the agent harder to test).
- The AI defaulted to the newest model (gemini-3.8-flash); you told it the model didn't need to be the latest, and the project switched to gemini-3.1-flash-lite, which was available and cheaper.
- Your own Module 2 example: you kept recurrence based on today's date when the AI called it a bug. -->
