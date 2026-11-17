"""Evaluate the AI Care Advisor end to end and print a pass/fail report.

Live cases call real Gemini (needs GEMINI_API_KEY in .env). Guardrail cases use
scripted bad LLM output so they are deterministic. Run:

    python evaluate.py            # live + guardrail cases
    python evaluate.py --offline  # guardrail cases only
"""

import json
import sys

from ai_advisor import load_knowledge, retrieve, setup_logging, suggest_tasks
from pawpal_system import Owner, Pet, Scheduler, Task

results: list[tuple[str, str, bool]] = []


def check(case: str, criterion: str, passed: bool) -> None:
    results.append((case, criterion, passed))
    print(f"    [{'PASS' if passed else 'FAIL'}] {criterion}")


def live_case(name: str, pet: Pet, preferences: dict | None = None, minutes: int = 90):
    """Retrieve -> Gemini -> guardrails -> accept all -> schedule, with checks."""
    print(f"\n=== {name}: {pet.name}, {pet.age}-year-old {pet.species}, preferences={preferences or {}}")
    if pet.tasks:
        print(f"  Existing tasks: {[t.title for t in pet.tasks]}")
    result = suggest_tasks(pet, preferences=preferences)
    retrieved = {p.id for p in result.passages}
    print(f"  Retrieved: {sorted(retrieved)}")
    if result.error:
        print(f"  Error: {result.error}")
    for s in result.suggestions:
        print(
            f"  Suggested: {s.title} ({s.duration_minutes} min, {s.priority.name.lower()}, "
            f"{s.frequency}, {s.time or 'flexible'}) <- {s.source}\n             {s.rationale}"
        )
    for item, why in result.rejected:
        title = item.get("title") if isinstance(item, dict) else item
        print(f"  Rejected by guardrails: {title!r} - {why}")

    existing = {t.title.lower() for t in pet.tasks}
    check(name, "advisor returned without error", result.error is None)
    check(name, "at least one suggestion accepted", bool(result.suggestions))
    check(name, "every suggestion cites a retrieved passage", all(s.source in retrieved for s in result.suggestions))
    check(name, "no suggestion duplicates an existing task", all(s.title.lower() not in existing for s in result.suggestions))

    for s in result.suggestions:
        pet.add_task(s.to_task())
    owner = Owner(name="Jordan", available_minutes=minutes, preferences=preferences or {})
    owner.add_pet(pet)
    scheduler = Scheduler(owner)
    plan = scheduler.build_daily_plan()
    print("  " + scheduler.explain_plan(plan).replace("\n", "\n  "))
    check(name, "plan includes an AI task citing its source", any("per " in i.reason for i in plan))
    return result


def scripted(raw: str):
    return lambda prompt: raw


def outage(prompt: str) -> str:
    raise RuntimeError("503 UNAVAILABLE (simulated outage)")


def guardrail_cases() -> None:
    print("\n=== Guardrail cases (scripted LLM output)")
    pet = Pet("Biscuit", "dog", age=4, tasks=[Task("Morning walk", 30)])
    source = retrieve(pet, load_knowledge())[0].id
    bad = {
        "tasks": [
            {"title": "Evening walk", "category": "walk", "duration_minutes": 30, "priority": "high",
             "frequency": "daily", "time": "18:00", "source": source, "rationale": "valid control item"},
            {"title": "Morning walk", "category": "walk", "duration_minutes": 30, "priority": "high",
             "frequency": "daily", "time": None, "source": source, "rationale": "duplicate"},
            {"title": "Agility class", "category": "enrichment", "duration_minutes": 45, "priority": "medium",
             "frequency": "weekly", "time": None, "source": "dog.md#made-up-section", "rationale": "hallucinated source"},
            {"title": "Midnight snack", "category": "feeding", "duration_minutes": 10, "priority": "low",
             "frequency": "daily", "time": "25:00", "source": source, "rationale": "invalid time"},
            {"title": "Marathon", "category": "walk", "duration_minutes": 600, "priority": "high",
             "frequency": "daily", "time": None, "source": source, "rationale": "too long"},
            {"title": "Late walk", "category": "walk", "duration_minutes": 60, "priority": "low",
             "frequency": "daily", "time": "23:30", "source": source, "rationale": "runs past midnight"},
        ]
    }
    r = suggest_tasks(pet, llm=scripted(json.dumps(bad)))
    reasons = {item["title"]: why for item, why in r.rejected}
    for title, why in reasons.items():
        print(f"  Rejected: {title!r} - {why}")
    print(f"  Accepted: {[s.title for s in r.suggestions]}")
    check("Guardrails", "valid item kept", [s.title for s in r.suggestions] == ["Evening walk"])
    check("Guardrails", "duplicate of existing task rejected", "Morning walk" in reasons)
    check("Guardrails", "hallucinated source rejected", "Agility class" in reasons)
    check("Guardrails", "invalid time 25:00 rejected", "Midnight snack" in reasons)
    check("Guardrails", "600-minute duration rejected", "Marathon" in reasons)
    check("Guardrails", "task past midnight rejected", "Late walk" in reasons)

    r = suggest_tasks(pet, llm=scripted("Sure! Here are some tasks: walk the dog."))
    print(f"  Non-JSON reply -> error: {r.error}")
    check("Guardrails", "non-JSON reply handled without crashing", bool(r.error) and not r.suggestions)

    r = suggest_tasks(pet, llm=outage)
    print(f"  Outage -> error: {r.error}")
    check("Guardrails", "API outage handled without crashing", bool(r.error) and not r.suggestions)

    cat_ids = [p.id for p in retrieve(Pet("Luna", "cat", age=12), load_knowledge())]
    print(f"  Senior cat retrieval: {cat_ids}")
    check("Guardrails", "cat retrieval excludes dog guidelines", not any(i.startswith("dog.md") for i in cat_ids))


def main() -> None:
    setup_logging()
    if "--offline" not in sys.argv:
        first = live_case("Case 1", Pet("Biscuit", "dog", age=4))
        live_case("Case 2", Pet("Luna", "cat", age=12), preferences={"notes": "senior cat, joint stiffness"})
        live_case("Case 3", Pet("Pip", "dog", age=0, tasks=[Task("Morning walk", 15)]), preferences={"notes": "new puppy, training"})

        print("\n=== Consistency: Case 1 run again")
        again = suggest_tasks(Pet("Biscuit", "dog", age=4))
        a = {s.source for s in first.suggestions}
        b = {s.source for s in again.suggestions}
        overlap = len(a & b) / len(a | b) if a | b else 0.0
        print(f"  Sources run 1: {sorted(a)}\n  Sources run 2: {sorted(b)}\n  Source overlap: {overlap:.0%}")
        check("Consistency", "repeat run cites mostly the same guidelines (>= 50% overlap)", overlap >= 0.5)
    guardrail_cases()

    passed = sum(ok for _, _, ok in results)
    print(f"\n=== Summary: {passed}/{len(results)} checks passed")
    for case, criterion, ok in results:
        if not ok:
            print(f"  FAILED: {case} - {criterion}")


if __name__ == "__main__":
    main()
