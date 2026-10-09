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
    pass_mark = st.number_input("Pass mark (%)", min_value=0.0, max_value=100.0, value=60.0, step=1.0)
    recipients_text = st.text_area(
        "Candidates (one per line: name,email,score_percent)",
        value="Asha Rao,asha@example.com,85\nRavi Kumar,ravi@example.com,52",
        height=140,
        help="Email is optional, but keep the comma before the score. Quote names that contain commas.",
    )
    submit = st.form_submit_button("Generate certificates", type="primary")


def parse_recipients(raw_text: str) -> list[dict[str, str]]:
    recipients = []
    for line_number, row in enumerate(csv.reader(io.StringIO(raw_text)), start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) != 3:
            raise ValueError(f"Line {line_number}: use name,email,score_percent.")
        name = row[0].strip()
        email = row[1].strip()
        if not name:
            raise ValueError(f"Line {line_number}: recipient name is required.")
        try:
            score_percent = float(row[2].strip())
        except ValueError as exc:
            raise ValueError(f"Line {line_number}: score must be a number from 0 to 100.") from exc
        if not 0 <= score_percent <= 100:
            raise ValueError(f"Line {line_number}: score must be between 0 and 100.")
        recipient = {"name": name}
        if email:
            recipient["email"] = email
        recipient["score_percent"] = score_percent
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
                "pass_mark": pass_mark,
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
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Job status", job["status"])
    col2.metric("Recipients", job["total"])
    col3.metric("Passed", job["passed"])
    col4.metric("Not passed", job["not_passed"])
    col5.metric("Certificates", job["succeeded"])
    col6.metric("Generation errors", job["failed"])
    st.code(job["id"], language=None)

    if job["succeeded"]:
        zip_key = f"certificates_zip_{job['id']}"
        if st.button("Prepare ZIP of passed-candidate certificates", key=f"prepare-{job['id']}"):
            try:
                zip_response = requests.get(f"{api_base}/api/jobs/{job['id']}/certificates.zip", timeout=60)
                zip_response.raise_for_status()
                st.session_state[zip_key] = zip_response.content
            except requests.RequestException as exc:
                st.error(f"Could not prepare certificate ZIP: {exc}")
        if zip_key in st.session_state:
            st.download_button(
                "Download passed-candidate certificates (.zip)",
                data=st.session_state[zip_key],
                file_name=f"certificates-{job['id']}.zip",
                mime="application/zip",
                key=f"download-zip-{job['id']}",
            )

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
            left.markdown(
                f"**{result['recipient_name']}** — {result['status']} "
                f"(score {result['score_percent']:g}%; pass mark {job['pass_mark']:g}%)"
            )
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
            elif result["status"] == "NOT_PASSED":
                left.warning(result.get("error") or "Candidate did not meet the pass mark.")
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
