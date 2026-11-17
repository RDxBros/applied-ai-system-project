"""AI Care Advisor: retrieve care-guideline passages and suggest tasks via Gemini."""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from pawpal_system import Pet, Priority, Task, parse_time

logger = logging.getLogger("pawpal")

GENERAL_FILES = ["feeding.md", "medication.md", "grooming_enrichment.md"]

TASK_SCHEMA = {
    "type": "object",
    "required": ["tasks"],
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "title",
                    "category",
                    "duration_minutes",
                    "priority",
                    "frequency",
                    "time",
                    "source",
                    "rationale",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "walk",
                            "feeding",
                            "meds",
                            "enrichment",
                            "grooming",
                            "health",
                            "other",
                        ],
                    },
                    "duration_minutes": {"type": "integer"},
                    "priority": {"type": "string", "enum": ["low", "medium", "high"]},
                    "frequency": {"type": "string", "enum": ["once", "daily", "weekly"]},
                    "time": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "source": {"type": "string"},
                    "rationale": {"type": "string"},
                },
            },
        }
    },
}

CATEGORIES = {"walk", "feeding", "meds", "enrichment", "grooming", "health", "other"}
PRIORITIES = {"low", "medium", "high"}
FREQUENCIES = {"once", "daily", "weekly"}

PROMPT_TEMPLATE = """You are PawPal+'s pet care advisor. Suggest care tasks for one pet, using ONLY the care guidelines provided below. Do not invent advice that is not supported by a guideline.

Pet: {name}, a {age}-year-old {species} ({life_stage}).
Owner preferences: {preferences}
Tasks the pet already has (do not repeat these): {existing}

Care guidelines (each starts with its source id in brackets):
{guidelines}

Rules:
- Suggest between 1 and {max_suggestions} tasks.
- Set "source" to the exact bracketed id of the guideline that supports the task.
- duration_minutes is a whole number from 1 to 240.
- Use a fixed "time" (24-hour HH:MM) only when the guideline calls for a specific time of day; otherwise use null.
- "rationale" is one short sentence explaining why this pet needs the task.
"""


class AdvisorError(Exception):
    """Raised for advisor failures the caller should surface as a friendly message."""


def setup_logging(path: str = "pawpal.log") -> None:
    """Attach one FileHandler to the 'pawpal' logger (safe to call on every rerun)."""
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(path)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


@dataclass
class Passage:
    id: str
    source: str
    heading: str
    text: str


def load_knowledge(directory: Path = Path(__file__).parent / "knowledge") -> list[Passage]:
    """Load every '## Heading' section of each markdown file as one Passage."""
    passages = []
    for path in sorted(Path(directory).glob("*.md")):
        heading = None
        body: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                if heading is not None:
                    passages.append(
                        Passage(
                            id=f"{path.name}#{_slug(heading)}",
                            source=path.name,
                            heading=heading,
                            text=" ".join(body).strip(),
                        )
                    )
                heading = line[3:].strip()
                body = []
            elif heading is not None:
                body.append(line.strip())
        if heading is not None:
            passages.append(
                Passage(
                    id=f"{path.name}#{_slug(heading)}",
                    source=path.name,
                    heading=heading,
                    text=" ".join(body).strip(),
                )
            )
    return passages


def life_stage(pet: Pet) -> str:
    """Bucket a pet's age into 'young', 'adult', or 'senior'."""
    if pet.age < 1:
        return "young"
    if pet.age >= 8:
        return "senior"
    return "adult"


_STAGE_WORDS = {
    "young": ["puppy", "kitten", "young"],
    "senior": ["senior", "older"],
    "adult": ["adult"],
}

_OTHER_SPECIES_FILE = {"dog": "cat.md", "cat": "dog.md", "other": {"dog.md", "cat.md"}}


def retrieve(
    pet: Pet, passages: list[Passage], k: int = 4, preferences: dict | None = None
) -> list[Passage]:
    """Score passages by keyword overlap with the pet and return the top k.

    Heading hits count double. Passages from the other species' file are
    excluded; if nothing scores, fall back to the general-care files.
    """
    species = pet.species.lower()
    excluded = _OTHER_SPECIES_FILE.get(species)
    if isinstance(excluded, str):
        excluded = {excluded}

    terms = [species, *_STAGE_WORDS[life_stage(pet)]]
    for value in (preferences or {}).values():
        terms.extend(_tokens(str(value)))

    candidates = [p for p in passages if p.source not in (excluded or set())]
    scored = []
    for p in candidates:
        heading_tokens = _tokens(p.heading)
        text_tokens = _tokens(p.text)
        score = 2 * sum(heading_tokens.count(t) for t in terms) + sum(
            text_tokens.count(t) for t in terms
        )
        scored.append((score, p))
    scored_positive = [sp for sp in scored if sp[0] > 0]
    if not scored_positive:
        scored_positive = [sp for sp in scored if sp[1].source in GENERAL_FILES]
    # sorted() is stable, so equal scores keep their load order
    return [p for _, p in sorted(scored_positive, key=lambda sp: -sp[0])][:k]


def build_prompt(
    pet: Pet, passages: list[Passage], preferences: dict | None = None, max_suggestions: int = 5
) -> str:
    """Fill the advisor prompt template for this pet and these passages."""
    prefs = ", ".join(f"{k}: {v}" for k, v in (preferences or {}).items()) or "none"
    existing = ", ".join(t.title for t in pet.tasks) or "none"
    guidelines = "\n\n".join(f"[{p.id}] {p.heading}\n{p.text}" for p in passages)
    return PROMPT_TEMPLATE.format(
        name=pet.name,
        age=pet.age,
        species=pet.species,
        life_stage=life_stage(pet),
        preferences=prefs,
        existing=existing,
        guidelines=guidelines,
        max_suggestions=max_suggestions,
    )


LLM = Callable[[str], str]


def gemini_llm(model: str | None = None, api_key: str | None = None) -> LLM:
    """Return a prompt->text callable backed by Gemini, or raise AdvisorError."""
    load_dotenv()
    key = api_key or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise AdvisorError("GEMINI_API_KEY is not set. Add it to .env.")
    model_name = model or os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=key)

    def call(prompt: str) -> str:
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=TASK_SCHEMA,
                temperature=0.2,
            ),
        )
        return response.text

    return call


@dataclass
class Suggestion:
    title: str
    category: str
    duration_minutes: int
    priority: Priority
    frequency: str
    time: str | None
    source: str
    rationale: str

    def to_task(self) -> Task:
        """Turn this suggestion into a real Task carrying its source id."""
        return Task(
            title=self.title,
            duration_minutes=self.duration_minutes,
            priority=self.priority,
            category=self.category,
            frequency=self.frequency,
            time=self.time,
            source=self.source,
        )


@dataclass
class AdvisorResult:
    suggestions: list[Suggestion]
    rejected: list[tuple[dict, str]]
    passages: list[Passage]
    error: str | None = None


def _valid_time(text) -> bool:
    if not isinstance(text, str):
        return False
    try:
        start = parse_time(text)
    except (ValueError, AttributeError):
        return False
    hours, _, minutes = text.partition(":")
    return len(hours) == 2 and len(minutes) == 2 and 0 <= start < 1440


def validate_suggestions(
    raw: str, passages: list[Passage], existing: list[Task], max_suggestions: int = 5
) -> tuple[list[Suggestion], list[tuple[dict, str]]]:
    """Apply guardrails to raw LLM JSON; returns (accepted, rejected-with-reasons)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AdvisorError(f"Gemini returned unparseable JSON: {exc}") from exc
    items = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AdvisorError('Gemini response is missing a "tasks" list.')

    source_ids = {p.id for p in passages}
    taken = {t.title.lower() for t in existing}
    accepted: list[Suggestion] = []
    rejected: list[tuple[dict, str]] = []

    for item in items:
        reason = None
        if not isinstance(item, dict):
            rejected.append(({"item": item}, "not an object"))
            continue
        if len(accepted) >= max_suggestions:
            reason = "over the suggestion limit"
        else:
            title = item.get("title")
            duration = item.get("duration_minutes")
            priority = item.get("priority")
            frequency = item.get("frequency")
            category = item.get("category")
            when = item.get("time")
            source = item.get("source")
            if not isinstance(title, str) or not title.strip() or len(title) > 60:
                reason = "missing or invalid title"
            elif not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 240:
                reason = "duration_minutes must be an integer from 1 to 240"
            elif not isinstance(priority, str) or priority.lower() not in PRIORITIES:
                reason = "invalid priority"
            elif not isinstance(frequency, str) or frequency.lower() not in FREQUENCIES:
                reason = "invalid frequency"
            elif when is not None and not _valid_time(when):
                reason = "invalid time"
            elif when is not None and parse_time(when) + duration > 1440:
                reason = "task would end after midnight"
            elif source not in source_ids:
                reason = "cites a source that was not retrieved"
            elif title.strip().lower() in taken:
                reason = "duplicates an existing task"
        if reason:
            rejected.append((item, reason))
            logger.warning("Rejected suggestion %r: %s", item, reason)
            continue
        cat = category.lower() if isinstance(category, str) else "other"
        if cat not in CATEGORIES:
            cat = "other"
        accepted.append(
            Suggestion(
                title=title.strip(),
                category=cat,
                duration_minutes=duration,
                priority=Priority[priority.upper()],
                frequency=frequency.lower(),
                time=when,
                source=source,
                rationale=str(item.get("rationale", "")),
            )
        )
        taken.add(title.strip().lower())
    return accepted, rejected


def suggest_tasks(
    pet: Pet, llm: LLM | None = None, preferences: dict | None = None, k: int = 4
) -> AdvisorResult:
    """Retrieve guidelines, ask the LLM for tasks, validate them. Never raises."""
    try:
        passages = retrieve(pet, load_knowledge(), k=k, preferences=preferences)
        if not passages:
            return AdvisorResult([], [], [], error="No care guidelines were found.")
        logger.info(
            "Advising %s (%s); retrieved %s", pet.name, pet.species, [p.id for p in passages]
        )
        prompt = build_prompt(pet, passages, preferences=preferences)
        if llm is None:
            llm = gemini_llm()
        start = time.monotonic()
        raw = llm(prompt)
        logger.info("LLM call took %.2fs", time.monotonic() - start)
        suggestions, rejected = validate_suggestions(raw, passages, pet.tasks)
        logger.info(
            "Advisor accepted %d suggestion(s), rejected %d",
            len(suggestions),
            len(rejected),
        )
        return AdvisorResult(suggestions, rejected, passages)
    except AdvisorError as exc:
        logger.error("Advisor failed: %s", exc)
        return AdvisorResult([], [], locals().get("passages", []), error=str(exc))
    except Exception as exc:
        logger.error("Advisor LLM call failed: %s", exc)
        return AdvisorResult(
            [], [], locals().get("passages", []), error=f"Could not reach Gemini: {exc}"
        )
