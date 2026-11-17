import json

import pytest

from ai_advisor import (
    AdvisorError,
    build_prompt,
    gemini_llm,
    life_stage,
    load_knowledge,
    retrieve,
    suggest_tasks,
    validate_suggestions,
)
from pawpal_system import Owner, Pet, Priority, Scheduler, Task


def make_item(sid, **overrides):
    item = {
        "title": "Evening walk",
        "category": "walk",
        "duration_minutes": 30,
        "priority": "high",
        "frequency": "daily",
        "time": None,
        "source": sid,
        "rationale": "Adult dogs need two daily walks.",
    }
    item.update(overrides)
    return item


@pytest.fixture
def passages():
    return load_knowledge()


@pytest.fixture
def dog_passages(passages):
    return retrieve(Pet("Biscuit", "dog", age=3), passages)


def test_load_knowledge_covers_all_files_with_unique_ids(passages):
    sources = {p.source for p in passages}
    assert sources == {
        "dog.md",
        "cat.md",
        "life_stages.md",
        "feeding.md",
        "medication.md",
        "grooming_enrichment.md",
    }
    ids = [p.id for p in passages]
    assert len(ids) == len(set(ids))
    assert all(p.text for p in passages)


def test_retrieve_excludes_other_species_and_ranks_walk(passages):
    top = retrieve(Pet("Biscuit", "dog", age=3), passages)
    assert all(p.source != "cat.md" for p in top)
    assert any(
        p.source == "dog.md"
        and ("walk" in p.heading.lower() or "exercise" in p.heading.lower())
        for p in top
    )


def test_retrieve_senior_dog_includes_senior_passage(passages):
    top = retrieve(Pet("Old Boy", "dog", age=10), passages, k=6)
    assert any("senior" in p.heading.lower() for p in top)


def test_retrieve_is_deterministic(passages):
    pet = Pet("Biscuit", "dog", age=3)
    assert [p.id for p in retrieve(pet, passages)] == [p.id for p in retrieve(pet, passages)]


def test_life_stage_buckets():
    assert life_stage(Pet("P", "dog", age=0)) == "young"
    assert life_stage(Pet("A", "dog", age=5)) == "adult"
    assert life_stage(Pet("S", "dog", age=8)) == "senior"


def test_build_prompt_includes_ids_and_existing_titles(dog_passages):
    pet = Pet("Biscuit", "dog", age=3)
    pet.add_task(Task("Morning walk", 30))
    prompt = build_prompt(pet, dog_passages, preferences={"walk time": "evening"})
    for p in dog_passages:
        assert p.id in prompt
    assert "Morning walk" in prompt
    assert "walk time: evening" in prompt


def test_valid_json_produces_suggestions_and_task_source(dog_passages):
    source = dog_passages[0].id
    raw = json.dumps({"tasks": [make_item(source)]})
    suggestions, rejected = validate_suggestions(raw, dog_passages, [])
    assert not rejected
    assert suggestions[0].priority is Priority.HIGH
    task = suggestions[0].to_task()
    assert task.source == source
    assert task.title == "Evening walk"


@pytest.mark.parametrize(
    "overrides",
    [
        {"duration_minutes": 0},
        {"duration_minutes": True},
        {"time": "25:00"},
        {"time": "23:30", "duration_minutes": 60},
        {"source": "dog.md#not-a-real-heading"},
        {"priority": "urgent"},
        {"frequency": "hourly"},
    ],
)
def test_guardrails_reject_bad_items(dog_passages, overrides):
    source = dog_passages[0].id
    bad = make_item(source, **overrides)
    good = make_item(source, title="Brush coat", category="grooming")
    raw = json.dumps({"tasks": [bad, good]})
    suggestions, rejected = validate_suggestions(raw, dog_passages, [])
    assert [s.title for s in suggestions] == ["Brush coat"]
    assert len(rejected) == 1 and rejected[0][0] == bad


def test_duplicate_of_existing_task_rejected(dog_passages):
    source = dog_passages[0].id
    raw = json.dumps({"tasks": [make_item(source, title="morning walk")]})
    suggestions, rejected = validate_suggestions(
        raw, dog_passages, [Task("Morning walk", 30)]
    )
    assert not suggestions and len(rejected) == 1


def test_unknown_category_coerced_to_other(dog_passages):
    source = dog_passages[0].id
    raw = json.dumps({"tasks": [make_item(source, category="swimming")]})
    suggestions, _ = validate_suggestions(raw, dog_passages, [])
    assert suggestions[0].category == "other"


def test_invalid_json_raises_advisor_error(dog_passages):
    with pytest.raises(AdvisorError):
        validate_suggestions("not json", dog_passages, [])


def test_suggest_tasks_with_fake_llm(dog_passages):
    source = dog_passages[0].id
    pet = Pet("Biscuit", "dog", age=3)
    llm = lambda prompt: json.dumps({"tasks": [make_item(source)]})
    result = suggest_tasks(pet, llm=llm)
    assert result.error is None
    assert len(result.suggestions) == 1
    assert result.passages


def test_suggest_tasks_invalid_json_sets_error():
    result = suggest_tasks(Pet("Biscuit", "dog", age=3), llm=lambda p: "oops")
    assert result.error and not result.suggestions


def test_suggest_tasks_llm_failure_sets_error():
    def boom(prompt):
        raise RuntimeError("network down")

    result = suggest_tasks(Pet("Biscuit", "dog", age=3), llm=boom)
    assert "Could not reach Gemini" in result.error


def test_gemini_llm_empty_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(AdvisorError):
        gemini_llm(api_key="")


def test_accepted_suggestion_flows_into_schedule(dog_passages):
    source = dog_passages[0].id
    pet = Pet("Biscuit", "dog", age=3)
    owner = Owner(name="Jordan")
    owner.add_pet(pet)
    llm = lambda prompt: json.dumps({"tasks": [make_item(source, time="09:00")]})
    result = suggest_tasks(pet, llm=llm)
    pet.add_task(result.suggestions[0].to_task())

    plan = Scheduler(owner).build_daily_plan()

    assert plan[0].task.title == "Evening walk"
    assert f"per {source}" in plan[0].reason
