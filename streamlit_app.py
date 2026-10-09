"""Simple browser UI for manually exercising the certificate API."""

import csv
import io
from datetime import date

import requests
import streamlit as st


st.set_page_config(page_title="Certificate Generator Tester", page_icon="🎓", layout="wide")
st.title("Bulk Certificate Generator")
st.caption("Testing interface for the FastAPI service. Start the API before using this page.")

api_base = st.sidebar.text_input("API base URL", value="http://127.0.0.1:8000").strip().rstrip("/")
st.sidebar.markdown(f"[Open API docs]({api_base}/docs)")

st.subheader("Create a generation job")
with st.form("create_job"):
    course_name = st.text_input("Course or event name", value="Python Fundamentals")
    completion_date = st.date_input("Completion date", value=date.today())
    recipients_text = st.text_area(
        "Recipients (one per line: name,email)",
        value="Asha Rao,asha@example.com\nRavi Kumar,ravi@example.com",
        height=140,
        help="Email is optional. If a name contains a comma, quote the name using CSV quotes.",
    )
    submit = st.form_submit_button("Generate certificates", type="primary")


def parse_recipients(raw_text: str) -> list[dict[str, str]]:
    recipients = []
    for line_number, row in enumerate(csv.reader(io.StringIO(raw_text)), start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) > 2:
            raise ValueError(f"Line {line_number}: use name,email (quote names that contain commas).")
        name = row[0].strip()
        email = row[1].strip() if len(row) == 2 else ""
        if not name:
            raise ValueError(f"Line {line_number}: recipient name is required.")
        recipient = {"name": name}
        if email:
            recipient["email"] = email
        recipients.append(recipient)
    if not recipients:
        raise ValueError("Enter at least one recipient.")
    return recipients


if submit:
    try:
        recipients = parse_recipients(recipients_text)
        response = requests.post(
            f"{api_base}/api/jobs/",
            json={
                "course_name": course_name,
                "completion_date": completion_date.isoformat(),
                "recipients": recipients,
            },
            timeout=120,
        )
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            st.error(f"API returned {response.status_code}: {detail}")
        else:
            st.session_state["last_job"] = response.json()
            st.success("Generation request completed.")
    except ValueError as exc:
        st.error(str(exc))
    except requests.RequestException as exc:
        st.error(f"Could not reach the API at {api_base}. Start FastAPI and check the URL. Details: {exc}")


job = st.session_state.get("last_job")
if job:
    st.divider()
    st.subheader("Latest job results")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Job status", job["status"])
    col2.metric("Recipients", job["total"])
    col3.metric("Succeeded", job["succeeded"])
    col4.metric("Failed", job["failed"])
    st.code(job["id"], language=None)

    refresh = st.button("Refresh job status")
    if refresh:
        try:
            response = requests.get(f"{api_base}/api/jobs/{job['id']}", timeout=30)
            response.raise_for_status()
            st.session_state["last_job"] = response.json()
            st.rerun()
        except requests.RequestException as exc:
            st.error(f"Could not refresh job status: {exc}")

    for result in job["results"]:
        with st.container(border=True):
            left, right = st.columns([3, 2])
            left.markdown(f"**{result['recipient_name']}** — {result['status']}")
            if result.get("recipient_email"):
                left.caption(result["recipient_email"])
            if result["status"] == "SUCCESS":
                try:
                    download = requests.get(
                        f"{api_base}/api/certificates/{result['certificate_id']}/download", timeout=30
                    )
                    download.raise_for_status()
                    right.download_button(
                        "Download PDF",
                        data=download.content,
                        file_name=f"certificate-{result['certificate_id']}.pdf",
                        mime="application/pdf",
                        key=f"download-{result['certificate_id']}",
                    )
                except requests.RequestException as exc:
                    right.error(f"Download unavailable: {exc}")
            elif result.get("error"):
                left.error(result["error"])

st.divider()
st.subheader("Look up an existing job")
job_id = st.text_input("Job ID")
if st.button("Fetch job"):
    if not job_id.strip():
        st.warning("Enter a job ID.")
    else:
        try:
            response = requests.get(f"{api_base}/api/jobs/{job_id.strip()}", timeout=30)
            if response.ok:
                st.session_state["last_job"] = response.json()
                st.rerun()
            st.error(f"API returned {response.status_code}: {response.text}")
        except requests.RequestException as exc:
            st.error(f"Could not reach the API: {exc}")
