import json
import re

from groq import Groq


DEFAULT_CRITERIA = [
    "Innovation",
    "Technical Execution",
    "Design and User Experience",
    "Impact and Feasibility",
    "Presentation and Clarity",
]


def get_groq_client(api_key: str) -> Groq:
    return Groq(api_key=api_key)


def _strip_code_fences(raw: str) -> str:
    return re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()


def evaluate_project(client: Groq, model: str, criteria: list, project: dict) -> dict:
    criteria_list = ", ".join(criteria)

    system_msg = (
        "You are JudgeGPT, an automated hackathon judging system. Your role "
        "is to evaluate submitted projects consistently and objectively "
        "against a fixed rubric, regardless of team size, presentation "
        "style, or prior reputation. Apply the same standard to every "
        "submission. Score each of the following criteria on a scale of "
        f"1 to 10: {criteria_list}. Provide specific, evidence-based "
        "justification tied to the submission details rather than generic "
        "praise or criticism. Respond with strict valid JSON only, no "
        "markdown fences, using this schema: "
        "{"
        '"scores": {"<criterion>": <integer 1-10>, ...}, '
        '"weighted_total": <number out of 100>, '
        '"summary": "<two to three sentence overall assessment>", '
        '"strengths": ["...", "..."], '
        '"weaknesses": ["...", "..."], '
        '"recommendation": "<advance to next round | needs revision | not competitive>"'
        "}"
    )

    user_msg = (
        f"Project title: {project.get('title', 'Untitled')}\n"
        f"Team size: {project.get('team_size', 'Not specified')}\n"
        f"Tech stack: {project.get('tech_stack', 'Not specified')}\n"
        f"Description: {project.get('description', '')}\n"
        f"Demo or repository notes: {project.get('demo_notes', 'None provided')}"
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.2,
        max_tokens=1200,
    )

    raw = _strip_code_fences(completion.choices[0].message.content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def generate_schedule(client: Groq, model: str, event_details: dict) -> dict:
    system_msg = (
        "You are HostGPT, an assistant for hackathon organizers. Generate a "
        "realistic, well-paced event schedule based on the organizer's "
        "constraints. Account for meals, breaks, judging, and closing "
        "ceremonies. Respond with strict valid JSON only, no markdown "
        "fences, using this schema: "
        "{"
        '"schedule": [{"time": "<start-end>", "activity": "<name>", '
        '"notes": "<short note>"}, ...]'
        "}"
    )

    user_msg = (
        f"Event name: {event_details.get('name', 'Untitled Event')}\n"
        f"Duration: {event_details.get('duration', 'Not specified')}\n"
        f"Start time: {event_details.get('start_time', 'Not specified')}\n"
        f"Number of participants: {event_details.get('participants', 'Not specified')}\n"
        f"Special requirements: {event_details.get('requirements', 'None')}"
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.4,
        max_tokens=1200,
    )

    raw = _strip_code_fences(completion.choices[0].message.content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def assign_tasks(client: Groq, model: str, tasks: str, team_members: str, constraints: str) -> dict:
    system_msg = (
        "You are HostGPT, an assistant for hackathon organizers. Assign "
        "administrative and operational tasks to team members based on "
        "their listed skills or roles, balancing workload fairly. Respond "
        "with strict valid JSON only, no markdown fences, using this "
        "schema: "
        "{"
        '"assignments": [{"member": "<name>", "tasks": ["...", "..."], '
        '"rationale": "<short reason for this assignment>"}, ...], '
        '"unassigned_tasks": ["..."]'
        "}"
    )

    user_msg = (
        f"Tasks to assign: {tasks}\n"
        f"Team members and their skills or roles: {team_members}\n"
        f"Constraints: {constraints or 'None'}"
    )

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=1000,
    )

    raw = _strip_code_fences(completion.choices[0].message.content)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_response": raw}


def host_chat_response(client: Groq, model: str, conversation: list, user_message: str) -> str:
    system_msg = (
        "You are HostGPT, a professional assistant supporting hackathon "
        "organizers with event coordination, logistics, communications, "
        "and administrative decisions. Give direct, practical, actionable "
        "answers grounded in standard hackathon operations."
    )

    messages = [{"role": "system", "content": system_msg}]
    messages.extend(conversation)
    messages.append({"role": "user", "content": user_message})

    completion = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.5,
        max_tokens=800,
    )

    return completion.choices[0].message.content.strip()
