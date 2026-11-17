import streamlit as st

from ai_advisor import setup_logging, suggest_tasks
from pawpal_system import Owner, Pet, Priority, Scheduler, Task, format_time, parse_time

setup_logging()

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

st.title("🐾 PawPal+")

st.caption(
    "Plan pet care across your pets — and let the AI Care Advisor suggest tasks "
    "grounded in real care guidelines."
)

st.divider()


# The "vault": create the Owner once, then reuse it on every rerun.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(name="Jordan")
owner = st.session_state.owner

st.subheader("Owner")
owner.name = st.text_input("Owner name", value=owner.name)
owner.available_minutes = st.number_input(
    "Minutes available today", min_value=10, max_value=1440, value=owner.available_minutes
)
notes = st.text_input(
    "Care notes for the AI advisor (optional)",
    value=owner.preferences.get("notes", ""),
    placeholder="senior cat, joint stiffness",
).strip()
if notes:
    owner.preferences["notes"] = notes
else:
    owner.preferences.pop("notes", None)

st.subheader("Add a Pet")
with st.form("add_pet_form", clear_on_submit=True):
    pet_name = st.text_input("Pet name")
    species = st.selectbox("Species", ["dog", "cat", "other"])
    if st.form_submit_button("Add pet"):
        if not pet_name.strip():
            st.error("Please enter a pet name.")
        elif any(p.name.lower() == pet_name.strip().lower() for p in owner.pets):
            st.error(f"You already have a pet named {pet_name.strip()}.")
        else:
            owner.add_pet(Pet(name=pet_name.strip(), species=species))
            st.success(f"Added {pet_name.strip()}!")

if owner.pets:
    st.write("Your pets:")
    st.table([{"name": p.name, "species": p.species, "tasks": len(p.tasks)} for p in owner.pets])
else:
    st.info("No pets yet. Add one above.")

st.subheader("Add a Task")
if owner.pets:
    with st.form("add_task_form", clear_on_submit=True):
        pet_choice = st.selectbox("For which pet?", [p.name for p in owner.pets])
        task_title = st.text_input("Task title", placeholder="Morning walk")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
        with col2:
            priority = st.selectbox("Priority", ["low", "medium", "high"], index=2)
        with col3:
            fixed_time = st.text_input("Fixed time (optional)", placeholder="HH:MM")
        with col4:
            frequency = st.selectbox("Repeats", ["once", "daily", "weekly"], index=1)
        if st.form_submit_button("Add task"):
            fixed_time = fixed_time.strip()
            try:
                if fixed_time:
                    parse_time(fixed_time)
            except ValueError:
                st.error("Time must look like 08:30.")
            else:
                if not task_title.strip():
                    st.error("Please enter a task title.")
                else:
                    pet = next(p for p in owner.pets if p.name == pet_choice)
                    pet.add_task(
                        Task(
                            title=task_title.strip(),
                            duration_minutes=int(duration),
                            priority=Priority[priority.upper()],
                            time=fixed_time or None,
                            frequency=frequency,
                        )
                    )
                    st.success(f"Added '{task_title.strip()}' for {pet_choice}.")
else:
    st.info("Add a pet first, then you can give it tasks.")

st.subheader("AI Care Advisor")
if owner.pets:
    advisor_pet_name = st.selectbox(
        "Get suggestions for which pet?", [p.name for p in owner.pets], key="advisor_pet"
    )
    advisor_pet = next(p for p in owner.pets if p.name == advisor_pet_name)
    if st.button("Suggest tasks"):
        with st.spinner("Asking Gemini for care suggestions..."):
            st.session_state.advisor_result = {
                "pet": advisor_pet_name,
                "result": suggest_tasks(advisor_pet, preferences=owner.preferences),
            }
    stored = st.session_state.get("advisor_result")
    if stored and stored["pet"] == advisor_pet_name:
        result = stored["result"]
        if result.error:
            st.error(result.error)
        checked = []
        for i, s in enumerate(result.suggestions):
            label = (
                f"**{s.title}** — {s.duration_minutes} min, {s.priority.name.lower()} priority, "
                f"{s.frequency}, {s.time or 'flexible'}\n\n{s.rationale} *(source: {s.source})*"
            )
            checked.append(st.checkbox(label, value=True, key=f"suggestion_{i}"))
        if result.suggestions and st.button("Add selected tasks"):
            added = 0
            for s, keep in zip(result.suggestions, checked):
                if keep:
                    advisor_pet.add_task(s.to_task())
                    added += 1
            del st.session_state.advisor_result
            st.success(f"Added {added} task{'s' if added != 1 else ''} for {advisor_pet.name}.")
            st.rerun()
        if result.rejected:
            with st.expander("Filtered out by guardrails"):
                for item, reason in result.rejected:
                    st.write(f"- `{item}` — {reason}")
        if result.passages:
            with st.expander("Guidelines used"):
                for p in result.passages:
                    st.write(f"**{p.id}** — {p.text}")
else:
    st.info("Add a pet first to get AI care suggestions.")

if owner.get_all_tasks():
    scheduler = Scheduler(owner)
    st.write("Current tasks:")

    conflicts = scheduler.detect_conflicts()
    if conflicts:
        st.warning(
            f"**{len(conflicts)} scheduling conflict{'s' if len(conflicts) > 1 else ''} found.** "
            "Two fixed-time tasks overlap, so only one of each pair can happen at its set time. "
            "Change one task's time (or remove it) to fix this:\n\n"
            + "\n".join(f"- {c}" for c in conflicts)
        )
    else:
        st.success("No time conflicts. Your fixed-time tasks all fit.")

    f1, f2 = st.columns(2)
    pet_filter = f1.selectbox("Filter by pet", ["All"] + [p.name for p in owner.pets])
    status_filter = f2.selectbox("Filter by status", ["All", "Pending", "Completed"])
    shown = scheduler.filter_tasks(
        pet_name=None if pet_filter == "All" else pet_filter,
        completed={"All": None, "Pending": False, "Completed": True}[status_filter],
    )
    if shown:
        st.caption("Sorted by time; flexible tasks are listed last.")
        st.table(
            [
                {
                    "pet": pet.name,
                    "task": task.title,
                    "minutes": task.duration_minutes,
                    "priority": task.priority.name.lower(),
                    "time": task.time or "flexible",
                    "repeats": task.frequency,
                    "due": str(task.due_date),
                    "done": task.completed,
                }
                for pet, task in scheduler.sort_by_time(shown)
            ]
        )
    else:
        st.info("No tasks match these filters.")

    pending = owner.filter_tasks(completed=False)
    if pending:
        labels = {f"{pet.name}: {task.title} ({task.due_date})": (pet, task) for pet, task in pending}
        choice = st.selectbox("Mark a task complete", list(labels))
        if st.button("Complete task"):
            pet, task = labels[choice]
            following = pet.complete_task(task)
            if following:
                st.success(f"Done! Next '{task.title}' scheduled for {following.due_date}.")
            else:
                st.success("Done!")
            st.rerun()

st.divider()

st.subheader("Build Schedule")
if st.button("Generate schedule"):
    if not owner.get_all_tasks():
        st.warning("Add at least one task first.")
    else:
        scheduler = Scheduler(owner)
        plan = scheduler.build_daily_plan()
        if plan:
            used = sum(item.task.duration_minutes for item in plan)
            st.success(
                f"Planned {len(plan)} task{'s' if len(plan) > 1 else ''}: "
                f"{used} of {owner.available_minutes} minutes used."
            )
            st.table(
                [
                    {
                        "start": format_time(item.start_minute),
                        "end": format_time(item.end_minute()),
                        "pet": item.pet.name,
                        "task": item.task.title,
                        "why": item.reason,
                    }
                    for item in plan
                ]
            )
        else:
            st.info("Nothing to schedule today.")
        if scheduler.skipped:
            st.warning(
                "**Couldn't fit these tasks:**\n\n"
                + "\n".join(
                    f"- {task.title} for {pet.name} ({task.duration_minutes} min): {why}"
                    for pet, task, why in scheduler.skipped
                )
                + "\n\nTry freeing up time, shortening a task, or moving a fixed time."
            )
