# AI-Powered Hackathon Management Platform

A Streamlit application combining two AI systems for hackathon operations:

- JudgeGPT: automated, consistent project evaluation against a fixed rubric.
- HostGPT: event scheduling, task assignment, and coordination support for organizers.

## Setup

1. Create a virtual environment and install dependencies:

   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

2. Get a Groq API key from https://console.groq.com/keys.

3. Run the app:

   streamlit run app.py

4. Enter the API key in the sidebar. It is kept only in the current session.

## Structure

- app.py: Streamlit UI and page logic.
- utils.py: Groq API integration for evaluation, scheduling, task assignment, and chat.
- requirements.txt: dependencies.

## Design notes

JudgeGPT uses a fixed rubric and low temperature to reduce variance between
evaluations of similar submissions, supporting consistency across a large
number of projects and judges. HostGPT uses a separate system prompt focused
on operational and logistical support rather than evaluation.

## Possible extensions

- Persist evaluations and chat history to a database instead of session state.
- Support multiple judges per project with score aggregation and outlier detection.
- Add authentication so only organizers can access HostGPT and only judges can access JudgeGPT.
- Add CSV or Google Sheets export for evaluation results and task assignments.
