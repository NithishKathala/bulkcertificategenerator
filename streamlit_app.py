"""Simple browser UI for manually exercising the certificate API."""

from datetime import date
import os
import time

import requests
import streamlit as st
from app.csv_import import parse_recipient_csv


st.set_page_config(page_title="Certificate Generator Tester", page_icon="🎓", layout="wide")
st.title("Bulk Certificate Generator")
st.caption("Testing interface for the FastAPI service. Start the API before using this page.")

api_base = st.sidebar.text_input(
    "API base URL",
    value=os.getenv("CERTIFICATE_API_BASE_URL", "http://127.0.0.1:8000"),
).strip().rstrip("/")
api_key = st.sidebar.text_input(
    "API key (optional)", value=os.getenv("CERTIFICATE_API_KEY", ""), type="password"
).strip()


def api_headers() -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def add_certificate_download(column, download_url: str, certificate_id: str, key: str) -> None:
    try:
        download = requests.get(f"{api_base}{download_url}", headers=api_headers(), timeout=30)
        download.raise_for_status()
        column.download_button(
            "Download certificate",
            data=download.content,
            file_name=f"certificate-{certificate_id}.pdf",
            mime="application/pdf",
            key=key,
        )
    except requests.RequestException as exc:
        column.error(f"Download unavailable: {exc}")


st.sidebar.markdown(f"[Open API docs]({api_base}/docs)")

st.subheader("Create a generation job")
st.caption("Paste recipient rows or upload a CSV. The CSV file takes priority when selected.")
st.download_button(
    "Download CSV template",
    data="name,email,score_percent\nAsha Rao,asha@example.com,85\nRavi Kumar,ravi@example.com,52\n",
    file_name="certificate_recipients_template.csv",
    mime="text/csv",
)
uploaded_csv = st.file_uploader("Upload recipients (.csv)", type=["csv"])
if uploaded_csv is not None:
    try:
        preview_text = uploaded_csv.getvalue().decode("utf-8-sig")
        preview_recipients = parse_recipient_csv(preview_text)
        st.success(f"CSV ready: {len(preview_recipients)} recipient rows passed the import checks.")
    except UnicodeDecodeError:
        st.error("CSV must be saved as UTF-8. Re-save it as CSV UTF-8 and upload it again.")
    except ValueError as exc:
        st.error(str(exc))
with st.form("create_job"):
    course_name = st.text_input("Course or event name", value="Python Fundamentals")
    completion_date = st.date_input("Completion date", value=date.today())
    pass_mark = st.number_input("Pass mark (%)", min_value=0.0, max_value=100.0, value=60.0, step=1.0)
    require_pass = st.checkbox(
        "Only issue certificates to candidates who meet the pass mark",
        value=False,
        help="Off: passing candidates get completion certificates and below-threshold candidates get participation certificates. On: only passing candidates get completion certificates.",
    )
    recipients_text = st.text_area(
        "Candidates (one per line: name[,email[,score_percent]])",
        value="Asha Rao,asha@example.com,85\nRavi Kumar,ravi@example.com,52",
        height=140,
        help="Email and score are optional. Format: name,email,score_percent. Quote names that contain commas.",
    )
    wait_for_completion = st.checkbox(
        "Wait for generation progress",
        value=True,
        help="Turn this off to return to the page immediately and use Refresh or Cancel while the job runs.",
    )
    submit = st.form_submit_button("Generate certificates", type="primary")

if submit:
    try:
        if uploaded_csv is not None:
            try:
                raw_recipients = uploaded_csv.getvalue().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV must be saved as UTF-8. Re-save it as CSV UTF-8 and upload it again.") from exc
        else:
            raw_recipients = recipients_text
        recipients = parse_recipient_csv(raw_recipients)
        response = requests.post(
            f"{api_base}/api/jobs/",
            headers=api_headers(),
            json={
                "course_name": course_name,
                "completion_date": completion_date.isoformat(),
                "pass_mark": pass_mark,
                "require_pass": require_pass,
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
            job = response.json()
            st.session_state["last_job"] = job
            progress = st.progress(job.get("progress_percent", 0))
            progress_text = st.empty()
            progress_text.caption(
                f"Processing {job.get('processed', 0)} of {job['total']} recipients..."
            )

            # POST returns the accepted job snapshot. Poll the status endpoint
            # while the server's background task records recipient outcomes.
            deadline = time.monotonic() + 600
            while (
                wait_for_completion
                and job["status"] in {"PROCESSING", "CANCELLING"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.4)
                status_response = requests.get(
                    f"{api_base}/api/jobs/{job['id']}", headers=api_headers(), timeout=30
                )
                status_response.raise_for_status()
                job = status_response.json()
                st.session_state["last_job"] = job
                progress.progress(job.get("progress_percent", 0))
                progress_text.caption(
                    f"Processed {job.get('processed', 0)} of {job['total']} recipients "
                    f"({job.get('progress_percent', 0)}%)."
                )

            if job["status"] == "PROCESSING":
                st.warning("The job is still running. Use Refresh job status to check again.")
            else:
                progress.progress(100)
                progress_text.empty()
                st.success(f"Generation finished: {job['status']}.")
    except ValueError as exc:
        st.error(str(exc))
    except requests.RequestException as exc:
        st.error(f"Could not reach the API at {api_base}. Start FastAPI and check the URL. Details: {exc}")


job = st.session_state.get("last_job")
if job:
    st.divider()
    st.subheader("Latest job results")
    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)
    col1.metric("Job status", job["status"])
    col2.metric("Recipients", job["total"])
    col3.metric("Passed", job["passed"])
    col4.metric("Not passed", job["not_passed"])
    col5.metric("Unscored", job.get("unscored", 0))
    col6.metric("Certificates", job["succeeded"])
    col7.metric("Generation errors", job["failed"])
    st.code(job["id"], language=None)
    if job["status"] in {"PROCESSING", "CANCELLING"}:
        st.progress(job.get("progress_percent", 0))
        st.caption(
            f"Processed {job.get('processed', 0)} of {job['total']} recipients. "
            "Cancellation finishes the current certificate and then stops the remaining batch."
        )
        if job["status"] == "PROCESSING" and st.button("Cancel remaining recipients", key=f"cancel-{job['id']}"):
            try:
                cancel_response = requests.post(
                    f"{api_base}/api/jobs/{job['id']}/cancel",
                    headers=api_headers(),
                    timeout=30,
                )
                cancel_response.raise_for_status()
                st.session_state["last_job"] = cancel_response.json()
                st.rerun()
            except requests.HTTPError as exc:
                st.error(f"Could not cancel the job: {exc.response.text}")
            except requests.RequestException as exc:
                st.error(f"Could not reach the API to cancel the job: {exc}")

    if job["failed"] and job["status"] != "PROCESSING":
        if st.button("Retry failed certificate generations", key=f"retry-{job['id']}"):
            try:
                retry_response = requests.post(
                    f"{api_base}/api/jobs/{job['id']}/retry-failed",
                    headers=api_headers(),
                    timeout=30,
                )
                retry_response.raise_for_status()
                st.session_state["last_job"] = retry_response.json()
                st.rerun()
            except requests.HTTPError as exc:
                st.error(f"Could not retry failed certificates: {exc.response.text}")
            except requests.RequestException as exc:
                st.error(f"Could not reach the API to retry failed certificates: {exc}")

    if job["succeeded"]:
        zip_key = f"certificates_zip_{job['id']}"
        if st.button("Prepare ZIP of generated certificates", key=f"prepare-{job['id']}"):
            try:
                zip_response = requests.get(
                    f"{api_base}/api/jobs/{job['id']}/certificates.zip",
                    headers=api_headers(),
                    timeout=60,
                )
                zip_response.raise_for_status()
                st.session_state[zip_key] = zip_response.content
            except requests.RequestException as exc:
                st.error(f"Could not prepare certificate ZIP: {exc}")
        if zip_key in st.session_state:
            st.download_button(
                "Download generated certificates (.zip)",
                data=st.session_state[zip_key],
                file_name=f"certificates-{job['id']}.zip",
                mime="application/zip",
                key=f"download-zip-{job['id']}",
            )

    refresh = st.button("Refresh job status")
    if refresh:
        try:
            response = requests.get(
                f"{api_base}/api/jobs/{job['id']}", headers=api_headers(), timeout=30
            )
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
            if result["score_percent"] is None:
                assessment_text = "no score provided"
            else:
                assessment_text = (
                    f"score {result['score_percent']:g}%; pass mark {job['pass_mark']:g}%"
                )
            left.markdown(
                f"**{result['recipient_name']}** — {result['status']} "
                f"({assessment_text})"
            )
            if result.get("certificate_type"):
                left.caption(f"{result['certificate_type'].title()} certificate")
            if result.get("recipient_email"):
                left.caption(result["recipient_email"])
            if result["status"] == "SUCCESS":
                try:
                    download = requests.get(
                        f"{api_base}/api/certificates/{result['certificate_id']}/download",
                        headers=api_headers(),
                        timeout=30,
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
            elif result["status"] == "CANCELLED":
                left.warning(result.get("error") or "Generation was cancelled.")
            elif result.get("error"):
                left.error(result["error"])

    if st.button("Load job audit history", key=f"events-{job['id']}"):
        try:
            events_response = requests.get(
                f"{api_base}/api/jobs/{job['id']}/events",
                headers=api_headers(),
                timeout=30,
            )
            events_response.raise_for_status()
            st.session_state[f"job_events_{job['id']}"] = events_response.json()["events"]
        except requests.RequestException as exc:
            st.error(f"Could not load job audit history: {exc}")
    audit_events = st.session_state.get(f"job_events_{job['id']}")
    if audit_events:
        with st.expander(f"Job audit history ({len(audit_events)} events)"):
            for event in audit_events:
                label = f"{event['created_at']} — {event['event_type']}"
                if event.get("certificate_id"):
                    label += f" — Certificate {event['certificate_id']}"
                if event.get("detail"):
                    label += f" — {event['detail']}"
                st.write(label)

st.divider()
st.subheader("Look up an existing job")
job_id = st.text_input("Job ID")
if st.button("Fetch job"):
    if not job_id.strip():
        st.warning("Enter a job ID.")
    else:
        try:
            response = requests.get(
                f"{api_base}/api/jobs/{job_id.strip()}", headers=api_headers(), timeout=30
            )
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
        "Certificate status", ["Any", "Processing", "Generated", "Not passed", "Generation failed", "Cancelled"]
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
        headers=api_headers(),
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
            "Processing": "PROCESSING",
            "Generated": "SUCCESS",
            "Not passed": "NOT_PASSED",
            "Generation failed": "FAILED",
            "Cancelled": "CANCELLED",
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
    grouped_results = {}
    for item in search_page["results"]:
        email = (item.get("recipient_email") or "").strip().casefold()
        # A name alone is not a safe identity: two people can share a name.
        identity = ("email", email) if email else ("certificate", item["certificate_id"])
        group_key = (identity, item["course_name"].strip().casefold())
        grouped_results.setdefault(group_key, []).append(item)

    st.caption(
        f"Found {search_page['total']} matching certificate records. "
        f"This page contains {len(grouped_results)} candidate/course groups."
    )
    for items in grouped_results.values():
        item = items[0]
        with st.container(border=True):
            st.markdown(f"**{item['recipient_name']}** — {item['course_name']}")
            st.caption(item.get("recipient_email") or "Email not provided")
            if len(items) == 1:
                single = items[0]
                result_col, action_col = st.columns([4, 2])
                if single["score_percent"] is None:
                    assessment_summary = "No score provided"
                else:
                    result_word = "Passed" if single["passed"] else "Not passed"
                    assessment_summary = f"{single['score_percent']:g}% — {result_word}"
                certificate_label = f" — {single['certificate_type'].title()} certificate" if single.get("certificate_type") else ""
                result_col.write(f"{assessment_summary} — {single['status']}{certificate_label}")
                result_col.caption(f"Completion date: {single['completion_date']} | Job: {single['job_id']}")
                if single.get("download_url"):
                    add_certificate_download(
                        action_col,
                        single["download_url"],
                        single["certificate_id"],
                        key=f"search-download-{single['certificate_id']}",
                    )
            else:
                st.info(f"{len(items)} certificates from separate generation jobs are grouped here.")
                with st.expander(f"Certificate history ({len(items)})"):
                    for history_item in items:
                        history_col, history_action = st.columns([4, 2])
                        history_score = (
                            f"{history_item['score_percent']:g}%"
                            if history_item["score_percent"] is not None
                            else "No score"
                        )
                        history_type = (
                            f" — {history_item['certificate_type'].title()} certificate"
                            if history_item.get("certificate_type")
                            else ""
                        )
                        history_col.write(
                            f"{history_item['completion_date']} — {history_item['status']} — "
                            f"{history_score}{history_type} — Job {history_item['job_id']}"
                        )
                        if history_item.get("download_url"):
                            add_certificate_download(
                                history_action,
                                history_item["download_url"],
                                history_item["certificate_id"],
                                key=f"search-history-download-{history_item['certificate_id']}",
                            )

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
