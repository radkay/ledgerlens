"""Streamlit views for the reviewer and the published artifacts."""

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import streamlit as st

from .outputs import prefill_document, prefill_pdf
from .workbench import Decision, available_actions, can_correct


def show_workbench(operator):
    st.header("Review workbench")
    documents = {doc.doc_id: doc for doc in operator.documents()}
    if not documents:
        st.info("No documents are available for review.")
        return
    doc_id = st.selectbox(
        "Document", list(documents),
        format_func=lambda doc_id: f"{documents[doc_id].status.replace('_', ' ').title()} · {doc_id[:12]}",
    )
    document = documents[doc_id]
    left, right = st.columns(2)
    with left:
        st.subheader("Masked PDF")
        pdf = operator.preview(doc_id)
        if pdf is None:
            st.info("A verified masked preview is unavailable.")
        else:
            st.pdf(pdf, height=540, key=f"pdf-{doc_id}")
    with right:
        st.subheader("Extracted fields")
        for field in document.fields:
            visible = field.masked()
            value = visible.final_value if visible.final_value is not None else visible.extracted_value
            st.text(f"{visible.field_name}  ·  {value if value is not None else '—'}")
            confidence = visible.confidence
            if confidence is None:
                st.badge("Confidence unreported", color="gray")
            else:
                color = "green" if confidence >= 0.8 else "orange" if confidence >= 0.5 else "red"
                st.badge(f"{confidence:.0%} confidence", color=color)
        if not document.fields:
            st.caption("No fields are available for the current extraction.")
        show_decisions(operator, document)


def show_decisions(operator, document):
    st.subheader("Decision")
    if message := st.session_state.pop("decision_saved", None):
        st.success(message)
    corrected_fields = {field.field_id: field.field_name for field in document.fields if can_correct(field)}
    reasons = operator.reasons()
    actions = [
        action for action in available_actions(document.status, reasons)
        if action != "correct_field" or corrected_fields
    ]
    if not actions:
        st.caption("No decisions are available for this document state.")
        return

    scope = f"{document.doc_id}-{document.extraction_id}-{document.state_at.isoformat()}"
    action = st.selectbox("Action", actions, format_func=lambda item: item.replace("_", " ").title(),
                          key=f"action-{scope}")
    with st.form(f"decision-{scope}-{action}", clear_on_submit=False):
        reason = st.selectbox("Reason", reasons[action])
        field_id = corrected_value = None
        if action == "correct_field":
            field_id = st.selectbox("Field", list(corrected_fields), format_func=corrected_fields.__getitem__)
            corrected_value = st.text_input("Corrected value", max_chars=512)
        if st.form_submit_button("Record decision"):
            draft = (document.doc_id, document.extraction_id, field_id, action, reason, corrected_value,
                     operator.actor_id)
            previous = st.session_state.get("pending_decision")
            if previous and draft == (
                previous.doc_id, previous.extraction_id, previous.field_id, previous.action,
                previous.reason_code, previous.corrected_value, previous.actor_id,
            ):
                decision = previous
            else:
                decision = Decision(uuid4().hex, *draft, datetime.now(UTC))
            try:
                operator.append_decision(decision, document.state_at)
            except (PermissionError, ValueError) as error:
                st.error(str(error))
            except OSError:
                st.session_state["pending_decision"] = decision
                st.error("The decision could not be confirmed. Retry the same submission to reuse its ID.")
            else:
                st.session_state.pop("pending_decision", None)
                st.session_state["decision_saved"] = (
                    "Recall queued for the next publication." if action == "recall" else "Decision recorded."
                )
                st.rerun()


def show_outputs(reader):
    st.header("Published outputs")
    publication = reader.latest_publication()
    if publication is None:
        st.info("Waiting for the first publication.")
        return
    st.caption(f"Publication {publication.publication_seq} · {publication.run_id}")
    run_id = publication.run_id

    st.subheader("Document lineage")
    direction = st.radio("Trace direction", ["Document → output", "Output → document"], horizontal=True)
    forward = direction == "Document → output"
    key = "doc_id" if forward else "gold_row_id"
    index = reader.lineage_keys(run_id, key)
    if index:
        selection = st.selectbox("Source document" if forward else "Gold row", index)
        field_links = reader.lineage(run_id, **{key: selection})
        if field_links:
            st.dataframe([asdict(row) for row in field_links], hide_index=True)
        else:
            st.caption("No field links for this selection.")
    else:
        st.caption("No document lineage has been published.")

    st.subheader("Pre-filled form")
    rows = reader.prefill(run_id)
    if not rows:
        st.caption("No pre-filled forms have been published.")
        return
    forms = sorted({(row.person_key, row.tax_year, row.form_type) for row in rows})
    selected = st.selectbox("Form", forms, format_func=lambda item: " · ".join(map(str, item)))
    form = [row for row in rows if (row.person_key, row.tax_year, row.form_type) == selected]
    st.table([{"Field": row.output_field, "Value": row.output_value} for row in form])
    st.download_button("Download PDF", lambda: prefill_pdf(publication, form),
                       file_name=f"prefill-{publication.publication_seq}.pdf", mime="application/pdf")
    st.download_button("Download JSON", prefill_document(publication, form),
                       file_name=f"prefill-{publication.publication_seq}.json", mime="application/json")
