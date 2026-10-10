"""RepoPilot Streamlit UI (spec 9.8) — built last.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from repopilot import config, pipeline
from repopilot.ui import AnswerView, ClaimView, build_answer_view

DEFAULT_QUESTION = "How does this system handle a large number of users?"


def main() -> None:
    st.set_page_config(page_title="RepoPilot", layout="centered")
    st.title("RepoPilot")
    st.caption(
        "Offline, evidence-first Q&A about a public GitHub repository. "
        f"Local model: {config.MODEL_NAME}. Every claim is checked against real files."
    )

    with st.form("ask"):
        url = st.text_input(
            "GitHub repository URL", placeholder="https://github.com/owner/repo"
        )
        question = st.text_input("Question", value=DEFAULT_QUESTION)
        inject_fake = st.checkbox("Demo: inject a fabricated citation", value=False)
        submitted = st.form_submit_button("Ask")

    if not submitted:
        return
    if not url.strip() or not question.strip():
        st.warning("Please enter both a repository URL and a question.")
        return

    with st.spinner("Cloning, gathering evidence, and asking the local model..."):
        result = pipeline.answer(url.strip(), question.strip(), inject_fake=inject_fake)

    render(build_answer_view(result))


def render(view: AnswerView) -> None:
    if view.error:
        st.error(view.error)
        return

    st.subheader(f"Intent: {view.intent or 'unknown'}")
    st.caption(f"Verifier: {view.badge}")

    st.markdown("### Answer")
    if not view.claims:
        st.info("No claims survived verification.")
    for claim in view.claims:
        _render_claim(claim)

    if view.missing:
        st.warning("Not found: " + ", ".join(view.missing))
    if view.notes:
        st.caption("Notes: " + "; ".join(view.notes))

    if view.cannot_confirm:
        st.markdown("### Cannot confirm from code")
        for line in view.cannot_confirm:
            st.markdown(f"- {line}")

    if view.dropped:
        st.markdown("### Dropped (failed verification)")
        for claim in view.dropped:
            suffix = f" - _{claim.warning}_" if claim.warning else ""
            st.markdown(f"- {claim.statement}{suffix}")


def _render_claim(claim: ClaimView) -> None:
    st.markdown(f"**[{claim.label}]** {claim.statement}")
    if claim.warning:
        st.warning(f"Unverified: {claim.warning}")
    for citation in claim.citations:
        with st.expander(citation.label):
            st.code(citation.block or "(no text)", language="text")


if __name__ == "__main__":
    main()
