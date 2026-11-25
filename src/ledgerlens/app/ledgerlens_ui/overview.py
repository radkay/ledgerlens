import time
from uuid import uuid4

import streamlit as st

from .dashboard import CONFIDENCE_BUCKETS, read_batch_activity, read_overview

POLL_INTERVAL_SECONDS = 2
POLL_WINDOW_SECONDS = 120


def show_overview(reader):
    publication, live_counts, review, confidence, accuracy = read_overview(reader)
    st.header("Overview")
    st.subheader("Live document states")
    st.bar_chart({"Document state": list(live_counts), "Documents": list(live_counts.values())},
                 x="Document state", y="Documents", sort=False)
    if publication is None:
        st.info("Waiting for the first publication.")
        return

    st.caption(f"Publication {publication.publication_seq} · {publication.run_id} · "
               f"{publication.published_at:%Y-%m-%d %H:%M UTC}")
    st.subheader("Review volume at publication")
    st.metric("Needs review", review.get("needs_review", 0))
    if review:
        st.table([{"State": state, "Documents": count} for state, count in review.items()])

    show_confidence(confidence)

    st.subheader("Extraction accuracy")
    if not accuracy:
        st.info("Accuracy is pending for this publication.")
    else:
        st.table([{
            "Form": row.form_type, "Variant": row.variant,
            "Exact matches": row.exact_match_count, "Expected fields": row.expected_count,
            "Accuracy": f"{row.exact_match_count / row.expected_count:.1%}" if row.expected_count else "—",
        } for row in accuracy])


def show_confidence(rows):
    st.subheader("Extraction confidence")
    if not rows:
        st.info("Confidence distribution is pending for this publication.")
        return

    forms = sorted({row.form_type for row in rows})
    form_type = st.selectbox("Confidence form type", forms)
    counts = {row.bucket: row.count for row in rows if row.form_type == form_type}
    st.metric("Released fields", sum(counts.values()))
    st.bar_chart({"Confidence": CONFIDENCE_BUCKETS, "Fields": [counts.get(bucket, 0) for bucket in CONFIDENCE_BUCKETS]},
                 x="Confidence", y="Fields", sort=False)


def show_batches(operator=None, authorize=lambda: False):
    st.subheader("Processing schedule and batch")
    if operator is None:
        st.caption("Schedule is available in authenticated operator mode.")
        st.button("Run batch", disabled=True)
        return

    try:
        schedule = operator.schedule()
    except (OSError, ValueError):
        st.error("The configured processing job is unavailable.")
        return
    if schedule is None:
        st.caption("No schedule configured.")
    else:
        state = "Paused" if schedule.paused else "Scheduled"
        st.caption(f"{state} · {schedule.expression} · {schedule.timezone}")

    @st.fragment(run_every=POLL_INTERVAL_SECONDS if st.session_state.get("batch_poll_until") else None)
    def batch_status():
        if not authorize():
            st.rerun()
        job_run_id = st.session_state.get("batch_job_run_id")
        deadline = st.session_state.get("batch_poll_until")
        try:
            activity = read_batch_activity(operator, job_run_id)
        except OSError:
            if deadline and time.monotonic() > deadline:
                del st.session_state["batch_poll_until"]
                st.rerun()
            st.error("Batch status is temporarily unavailable.")
            return
        if activity.job_run_id != job_run_id:
            st.session_state["batch_job_run_id"] = activity.job_run_id
            if activity.active:
                st.session_state["batch_poll_until"] = time.monotonic() + POLL_WINDOW_SECONDS
                st.rerun()
        if activity.job_run_id:
            event = activity.event
            status = event.status.replace("_", " ").capitalize() if event else "Waiting for pipeline events"
            st.write(f"Batch {activity.job_run_id} · {status}")
            if event:
                st.caption(f"{event.trigger_type.capitalize()} · {event.event_at:%Y-%m-%d %H:%M:%S UTC}")
            if activity.terminal_job_state:
                state = activity.terminal_job_state.replace("_", " ").title()
                st.caption(f"Databricks job ended · {state}")
        if deadline and (not activity.active or time.monotonic() > deadline):
            del st.session_state["batch_poll_until"]
            st.rerun()
        if activity.active and not deadline and st.button("Resume status refresh"):
            st.session_state["batch_poll_until"] = time.monotonic() + POLL_WINDOW_SECONDS
            st.rerun()
        if st.button("Run batch", disabled=activity.active):
            request_id = st.session_state.setdefault("batch_request_id", uuid4().hex)
            try:
                st.session_state["batch_job_run_id"] = operator.run_batch(request_id)
            except (OSError, ValueError):
                st.error("Batch submission could not be confirmed. Retrying uses the same request ID.")
            else:
                del st.session_state["batch_request_id"]
                st.session_state["batch_poll_until"] = time.monotonic() + POLL_WINDOW_SECONDS
                st.rerun()

    batch_status()
