import streamlit as st

from utils import (
    DEFAULT_CRITERIA,
    get_groq_client,
    evaluate_project,
    generate_schedule,
    assign_tasks,
    host_chat_response,
)

st.set_page_config(
    page_title="Hackathon Management Platform",
    layout="wide",
)

if "groq_api_key" not in st.session_state:
    st.session_state.groq_api_key = ""

if "host_chat_history" not in st.session_state:
    st.session_state.host_chat_history = []

if "evaluations" not in st.session_state:
    st.session_state.evaluations = []

with st.sidebar:
    st.title("Configuration")

    api_key_input = st.text_input(
        "Groq API Key",
        type="password",
        value=st.session_state.groq_api_key,
    )
    st.session_state.groq_api_key = api_key_input

    llm_model = st.selectbox(
        "Model",
        ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"],
        index=0,
    )

    st.markdown("---")
    st.caption(
        "JudgeGPT provides consistent, rubric-based project evaluation. "
        "HostGPT supports event scheduling, task assignment, and general "
        "coordination for hackathon organizers."
    )

if not st.session_state.groq_api_key:
    st.warning("Enter your Groq API key in the sidebar to get started.")

st.title("AI-Powered Hackathon Management Platform")

tab_judge, tab_schedule, tab_tasks, tab_chat = st.tabs(
    ["JudgeGPT", "Event Schedule", "Task Assignment", "HostGPT Chat"]
)

with tab_judge:
    st.subheader("Automated Project Evaluation")

    col1, col2 = st.columns(2)
    with col1:
        title = st.text_input("Project title")
        team_size = st.text_input("Team size")
        tech_stack = st.text_input("Tech stack")
    with col2:
        demo_notes = st.text_area("Demo or repository notes", height=100)

    description = st.text_area("Project description", height=180)

    if st.button("Evaluate Project", type="primary"):
        if not st.session_state.groq_api_key:
            st.error("Add your Groq API key in the sidebar first.")
        elif not description.strip():
            st.info("Enter a project description first.")
        else:
            with st.spinner("Evaluating submission"):
                try:
                    client = get_groq_client(st.session_state.groq_api_key)
                    project = {
                        "title": title,
                        "team_size": team_size,
                        "tech_stack": tech_stack,
                        "description": description,
                        "demo_notes": demo_notes,
                    }
                    result = evaluate_project(client, llm_model, DEFAULT_CRITERIA, project)
                except Exception as e:
                    st.error(f"Evaluation failed: {e}")
                    result = None

            if result:
                if "raw_response" in result:
                    st.markdown(result["raw_response"])
                else:
                    scores = result.get("scores", {})
                    cols = st.columns(len(scores) + 1 if scores else 1)
                    for i, (k, v) in enumerate(scores.items()):
                        cols[i].metric(k, v)
                    cols[-1].metric("Weighted Total", result.get("weighted_total", "N/A"))

                    st.markdown("#### Summary")
                    st.write(result.get("summary", "N/A"))

                    st.markdown("#### Strengths")
                    for s in result.get("strengths", []):
                        st.write(f"- {s}")

                    st.markdown("#### Weaknesses")
                    for w in result.get("weaknesses", []):
                        st.write(f"- {w}")

                    st.markdown("#### Recommendation")
                    st.write(result.get("recommendation", "N/A"))

                    st.session_state.evaluations.append(
                        {"title": title or "Untitled", "result": result}
                    )

    if st.session_state.evaluations:
        st.markdown("---")
        st.markdown("#### Evaluation History")
        for entry in reversed(st.session_state.evaluations):
            total = entry["result"].get("weighted_total", "N/A")
            st.write(f"{entry['title']} — Weighted Total: {total}")

with tab_schedule:
    st.subheader("Event Schedule Generator")

    name = st.text_input("Event name")
    duration = st.text_input("Duration", placeholder="e.g. 36 hours")
    start_time = st.text_input("Start time", placeholder="e.g. Saturday 9:00 AM")
    participants = st.text_input("Number of participants")
    requirements = st.text_area("Special requirements", height=100)

    if st.button("Generate Schedule", type="primary"):
        if not st.session_state.groq_api_key:
            st.error("Add your Groq API key in the sidebar first.")
        elif not name.strip():
            st.info("Enter an event name first.")
        else:
            with st.spinner("Building schedule"):
                try:
                    client = get_groq_client(st.session_state.groq_api_key)
                    event_details = {
                        "name": name,
                        "duration": duration,
                        "start_time": start_time,
                        "participants": participants,
                        "requirements": requirements,
                    }
                    result = generate_schedule(client, llm_model, event_details)
                except Exception as e:
                    st.error(f"Schedule generation failed: {e}")
                    result = None

            if result:
                if "raw_response" in result:
                    st.markdown(result["raw_response"])
                else:
                    for item in result.get("schedule", []):
                        st.write(
                            f"**{item.get('time', '')}** — {item.get('activity', '')}"
                        )
                        if item.get("notes"):
                            st.caption(item["notes"])

with tab_tasks:
    st.subheader("Task Assignment")

    tasks = st.text_area(
        "Tasks to assign",
        placeholder="e.g. registration desk, sponsor liaison, judging logistics, AV setup",
        height=100,
    )
    team_members = st.text_area(
        "Team members and their skills or roles",
        placeholder="e.g. Sara: logistics and vendor management, Ali: technical support",
        height=100,
    )
    constraints = st.text_area("Constraints", height=80)

    if st.button("Assign Tasks", type="primary"):
        if not st.session_state.groq_api_key:
            st.error("Add your Groq API key in the sidebar first.")
        elif not tasks.strip() or not team_members.strip():
            st.info("Enter both tasks and team members first.")
        else:
            with st.spinner("Assigning tasks"):
                try:
                    client = get_groq_client(st.session_state.groq_api_key)
                    result = assign_tasks(client, llm_model, tasks, team_members, constraints)
                except Exception as e:
                    st.error(f"Task assignment failed: {e}")
                    result = None

            if result:
                if "raw_response" in result:
                    st.markdown(result["raw_response"])
                else:
                    for assignment in result.get("assignments", []):
                        st.markdown(f"**{assignment.get('member', '')}**")
                        for t in assignment.get("tasks", []):
                            st.write(f"- {t}")
                        if assignment.get("rationale"):
                            st.caption(assignment["rationale"])

                    unassigned = result.get("unassigned_tasks", [])
                    if unassigned:
                        st.markdown("#### Unassigned Tasks")
                        for t in unassigned:
                            st.write(f"- {t}")

with tab_chat:
    st.subheader("HostGPT Coordination Chat")

    for msg in st.session_state.host_chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_input = st.chat_input("Ask HostGPT about event coordination")

    if user_input:
        if not st.session_state.groq_api_key:
            st.error("Add your Groq API key in the sidebar first.")
        else:
            st.session_state.host_chat_history.append(
                {"role": "user", "content": user_input}
            )
            with st.chat_message("user"):
                st.write(user_input)

            with st.spinner("HostGPT is responding"):
                try:
                    client = get_groq_client(st.session_state.groq_api_key)
                    conversation = [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.host_chat_history[:-1]
                    ]
                    reply = host_chat_response(
                        client, llm_model, conversation, user_input
                    )
                except Exception as e:
                    reply = f"Request failed: {e}"

            st.session_state.host_chat_history.append(
                {"role": "assistant", "content": reply}
            )
            with st.chat_message("assistant"):
                st.write(reply)
