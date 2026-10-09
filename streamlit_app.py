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

    visible_results = job["results"][:25]
    if len(job["results"]) > len(visible_results):
        st.info(
            f"Showing the first {len(visible_results)} of {len(job['results'])} results. "
            "Use candidate search below to find and download a specific certificate."
        )
    for result in visible_results:
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

st.divider()
st.subheader("Search candidates and certificates")
with st.form("certificate_search"):
    search_col1, search_col2 = st.columns(2)
    search_text = search_col1.text_input("Name, email, or course contains")
    search_course = search_col2.text_input("Course filter")
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    assessment_filter = filter_col1.selectbox("Assessment result", ["Any", "Passed", "Not passed"])
    certificate_filter = filter_col2.selectbox(
        "Certificate status", ["Any", "Generated", "Not passed", "Generation failed"]
    )
    page_size = filter_col3.selectbox("Results per page", [25, 50, 100, 200], index=1)
    score_col1, score_col2 = st.columns(2)
    minimum_score = score_col1.number_input("Minimum score (optional)", min_value=0.0, max_value=100.0, value=0.0)
    maximum_score = score_col2.number_input("Maximum score (optional)", min_value=0.0, max_value=100.0, value=100.0)
    search_submitted = st.form_submit_button("Search", type="primary")


def load_search_page(params: dict, offset: int) -> None:
    response = requests.get(
        f"{api_base}/api/certificates/search",
        params={**params, "offset": offset, "limit": params["limit"]},
        timeout=30,
    )
    response.raise_for_status()
    st.session_state["certificate_search_results"] = response.json()


if search_submitted:
    search_params = {"limit": page_size}
    if search_text.strip():
        search_params["q"] = search_text.strip()
    if search_course.strip():
        search_params["course"] = search_course.strip()
    if assessment_filter != "Any":
        search_params["passed"] = assessment_filter == "Passed"
    if certificate_filter != "Any":
        search_params["status"] = {
            "Generated": "SUCCESS",
            "Not passed": "NOT_PASSED",
            "Generation failed": "FAILED",
        }[certificate_filter]
    if minimum_score > 0:
        search_params["score_min"] = minimum_score
    if maximum_score < 100:
        search_params["score_max"] = maximum_score
    st.session_state["certificate_search_params"] = search_params
    st.session_state["certificate_search_offset"] = 0
    try:
        load_search_page(search_params, 0)
    except requests.RequestException as exc:
        st.error(f"Search failed: {exc}")


search_page = st.session_state.get("certificate_search_results")
if search_page:
    st.caption(f"Found {search_page['total']} matching candidates. Showing {len(search_page['results'])} on this page.")
    for item in search_page["results"]:
        with st.container(border=True):
            result_col, action_col = st.columns([4, 2])
            result_col.markdown(
                f"**{item['recipient_name']}** — {item['course_name']} — {item['score_percent']:g}% — "
                f"{'Passed' if item['passed'] else 'Not passed'} — {item['status']}"
            )
            result_col.caption(
                f"Email: {item.get('recipient_email') or 'not provided'} | "
                f"Completion date: {item['completion_date']} | Job: {item['job_id']}"
            )
            if item.get("download_url"):
                action_col.link_button("Download certificate", f"{api_base}{item['download_url']}")

    page_col1, page_col2, page_col3 = st.columns([1, 2, 1])
    offset = st.session_state.get("certificate_search_offset", 0)
    limit = search_page["limit"]
    if page_col1.button("Previous", disabled=offset == 0, key="search_previous"):
        new_offset = max(0, offset - limit)
        try:
            load_search_page(st.session_state["certificate_search_params"], new_offset)
            st.session_state["certificate_search_offset"] = new_offset
            st.rerun()
        except requests.RequestException as exc:
            st.error(f"Could not load the previous page: {exc}")
    page_col2.caption(f"Showing results {offset + 1}–{min(offset + len(search_page['results']), search_page['total'])}")
    if page_col3.button(
        "Next", disabled=offset + limit >= search_page["total"], key="search_next"
    ):
        new_offset = offset + limit
        try:
            load_search_page(st.session_state["certificate_search_params"], new_offset)
            st.session_state["certificate_search_offset"] = new_offset
            st.rerun()
        except requests.RequestException as exc:
            st.error(f"Could not load the next page: {exc}")
