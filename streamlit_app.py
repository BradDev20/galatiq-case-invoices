"""Streamlit UI for the invoice workflow demo."""

from pathlib import Path

import streamlit as st

from src.presentation import (
    describe_workflow_error,
    format_log_line,
    format_outcome_label,
    stage_overview,
)
from src.state import AgentState
from src.services.workflow_runner import stream_invoice_workflow
from src.ui_support import save_uploaded_invoice


st.set_page_config(
    page_title="Galatiq Invoice Processing",
    page_icon=":page_facing_up:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@500&display=swap');
    :root {
        --ink-strong: #162532;
        --ink: #254052;
        --ink-soft: #5c7281;
        --surface: #ffffff;
        --surface-muted: #f6f8fa;
        --surface-accent: #eef3f7;
        --line: #d5dee6;
        --line-strong: #beccd6;
        --navy: #163247;
        --teal: #1f6f63;
        --amber: #9a641f;
        --rose: #8d3944;
        --slate: #446274;
    }
    .stApp {
        background:
            radial-gradient(circle at top left, rgba(45, 106, 120, 0.08), transparent 28%),
            linear-gradient(180deg, #edf2f5 0%, #f7f9fb 100%);
        color: var(--ink-strong);
        font-family: "Public Sans", "Segoe UI", sans-serif;
    }
    .stApp, .stMarkdown, .stText, p, li, label, div {
        font-family: "Public Sans", "Segoe UI", sans-serif;
    }
    .stApp h1, .stApp h2, .stApp h3 {
        letter-spacing: -0.03em;
        color: var(--ink-strong);
    }
    .main .block-container {
        max-width: 1180px;
        padding-top: 2rem;
        padding-bottom: 2.5rem;
    }
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #163247 0%, #1f4159 100%);
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }
    [data-testid="stSidebar"] > div:first-child {
        padding-top: 1.2rem;
    }
    [data-testid="stSidebar"] * {
        color: #f3f7fa;
    }
    [data-testid="stSidebar"] .stRadio label,
    [data-testid="stSidebar"] .stSelectbox label,
    [data-testid="stSidebar"] .stFileUploader label,
    [data-testid="stSidebar"] .stMarkdown,
    [data-testid="stSidebar"] .stCaption {
        color: #f3f7fa;
    }
    [data-testid="stSidebar"] .stButton button {
        background: linear-gradient(135deg, #d98a2b 0%, #c5701d 100%);
        color: #ffffff;
        border: none;
        border-radius: 12px;
        font-weight: 700;
        min-height: 2.8rem;
        box-shadow: 0 10px 24px rgba(9, 20, 28, 0.22);
    }
    .app-shell {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 1.45rem 1.55rem;
        box-shadow: 0 18px 40px rgba(16, 37, 51, 0.08);
        margin-bottom: 1.15rem;
    }
    .hero-kicker {
        color: #5d7485;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.76rem;
        font-weight: 700;
    }
    .hero-title {
        font-size: clamp(2.2rem, 3vw, 3.15rem);
        line-height: 1.08;
        font-weight: 800;
        color: var(--ink-strong);
        margin: 0.42rem 0 0.75rem 0;
        max-width: 16ch;
    }
    .hero-copy {
        color: var(--ink);
        font-size: 1.02rem;
        line-height: 1.62;
        margin: 0;
        max-width: 60rem;
    }
    .top-grid {
        display: grid;
        grid-template-columns: minmax(0, 0.4fr) minmax(0, 0.6fr);
        gap: 1rem;
        align-items: stretch;
        margin-bottom: 1.1rem;
    }
    .top-panel {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 1.35rem 1.45rem;
        box-shadow: 0 18px 40px rgba(16, 37, 51, 0.08);
        min-height: 100%;
    }
    .top-panel.hero {
        display: flex;
        flex-direction: column;
        justify-content: center;
    }
    .top-panel.summary {
        color: #ffffff;
        border: none;
        box-shadow: 0 18px 36px rgba(16, 37, 51, 0.14);
    }
    .top-panel.summary.success { background: linear-gradient(135deg, #176756 0%, #249177 100%); }
    .top-panel.summary.warning { background: linear-gradient(135deg, #d97e89 0%, #efb1b8 100%); }
    .top-panel.summary.error { background: linear-gradient(135deg, #7d2f3d 0%, #b24a59 100%); }
    .top-panel.summary.info { background: linear-gradient(135deg, #24465f 0%, #3d6f90 100%); }
    .top-panel.summary.processing { background: linear-gradient(135deg, #cb9a21 0%, #e7c257 100%); }
    .top-panel.summary.full-span {
        grid-column: 1 / -1;
    }
    .summary-card {
        border-radius: 22px;
        padding: 1.25rem 1.3rem;
        color: #ffffff;
        margin-bottom: 1.1rem;
        box-shadow: 0 18px 36px rgba(16, 37, 51, 0.14);
    }
    .summary-card.success { background: linear-gradient(135deg, #176756 0%, #249177 100%); }
    .summary-card.warning { background: linear-gradient(135deg, #8b5819 0%, #bb7b28 100%); }
    .summary-card.error { background: linear-gradient(135deg, #7d2f3d 0%, #b24a59 100%); }
    .summary-card.info { background: linear-gradient(135deg, #24465f 0%, #3d6f90 100%); }
    .summary-label {
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.76rem;
        opacity: 0.9;
        font-weight: 700;
    }
    .summary-title {
        font-size: 2rem;
        font-weight: 800;
        margin: 0.24rem 0 0.58rem 0;
        letter-spacing: -0.03em;
    }
    .summary-copy {
        font-size: 1rem;
        margin: 0;
        line-height: 1.55;
        max-width: 62rem;
    }
    .summary-meta {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.75rem;
        margin-top: 1rem;
    }
    .summary-meta-card {
        background: rgba(255, 255, 255, 0.12);
        border: 1px solid rgba(255, 255, 255, 0.16);
        border-radius: 16px;
        padding: 0.85rem 0.9rem;
    }
    .summary-meta-label {
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-size: 0.72rem;
        opacity: 0.86;
        font-weight: 700;
        margin-bottom: 0.28rem;
    }
    .summary-meta-value {
        font-size: 1rem;
        line-height: 1.35;
        font-weight: 700;
    }
    .sidebar-panel {
        background: rgba(255, 255, 255, 0.08);
        border: 1px solid rgba(255, 255, 255, 0.12);
        border-radius: 18px;
        padding: 0.9rem 0.95rem;
        margin-bottom: 0.9rem;
    }
    .sidebar-title {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        opacity: 0.85;
        font-weight: 700;
        margin-bottom: 0.45rem;
    }
    .sidebar-headline {
        font-size: 1.02rem;
        font-weight: 700;
        margin-bottom: 0.28rem;
        letter-spacing: -0.02em;
    }
    .sidebar-copy {
        color: rgba(243, 247, 250, 0.86);
        font-size: 0.9rem;
        line-height: 1.55;
        margin: 0;
    }
    .stage-panel {
        background: linear-gradient(180deg, #fbfcfd 0%, #f4f7f9 100%);
        border: 1px solid #d7e2e9;
        border-radius: 16px;
        padding: 0.95rem 0.95rem 0.9rem 0.95rem;
        margin-bottom: 0.75rem;
        position: relative;
        overflow: hidden;
    }
    .stage-panel::before {
        content: "";
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        width: 4px;
        background: linear-gradient(180deg, #305d79 0%, #4c819f 100%);
    }
    .stage-label {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #617887;
        margin-bottom: 0.32rem;
        font-weight: 700;
    }
    .stage-status {
        font-size: 1rem;
        font-weight: 800;
        color: var(--ink-strong);
        margin-bottom: 0.34rem;
        letter-spacing: -0.02em;
    }
    .stage-status-completed {
        color: #1d7a60;
    }
    .stage-status-rejected {
        color: #a33b4e;
    }
    .stage-status-pending {
        color: #7b5e11;
    }
    .stage-reason {
        color: var(--ink);
        font-size: 0.92rem;
        line-height: 1.5;
        margin: 0;
    }
    .status-shell {
        background: rgba(255, 255, 255, 0.96);
        border: 1px solid var(--line);
        border-radius: 22px;
        padding: 1.2rem 1.25rem;
        box-shadow: 0 18px 40px rgba(16, 37, 51, 0.08);
    }
    .top-status-shell {
        background: rgba(255, 255, 255, 0.98);
        border: 1px solid #cad7e0;
        border-radius: 20px;
        padding: 1.1rem 1.2rem 1rem 1.2rem;
        box-shadow: 0 14px 30px rgba(16, 37, 51, 0.08);
        margin-bottom: 1rem;
    }
    .top-status-kicker {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #657d8d;
        font-weight: 700;
        margin-bottom: 0.55rem;
    }
    .top-status-banner {
        border-radius: 16px;
        padding: 0.95rem 1rem;
        border: 1px solid transparent;
        margin-bottom: 0.95rem;
    }
    .top-status-banner-title {
        font-size: 1.15rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        margin-bottom: 0.25rem;
    }
    .top-status-banner-copy {
        font-size: 0.95rem;
        line-height: 1.5;
        margin: 0;
    }
    .top-status-banner.success {
        background: #edf8f3;
        border-color: #bfe2cf;
        color: #195c46;
    }
    .top-status-banner.warning {
        background: #fbeef1;
        border-color: #e3a2ae;
        color: #a33b4e;
    }
    .top-status-banner.warning .top-status-banner-title {
        color: #a33b4e !important;
    }
    .top-status-banner.error {
        background: #fdeff1;
        border-color: #efc3cb;
        color: #8c3042;
    }
    .top-status-banner.processing {
        background: #fff7dc;
        border-color: #ead28a;
        color: #6e5508;
    }
    .top-status-banner.info {
        background: #eef4f8;
        border-color: #cadbe8;
        color: #23465f;
    }
    .top-status-details {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 0.8rem 1.2rem;
    }
    .top-status-detail-label {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #728898;
        font-weight: 700;
        margin-bottom: 0.2rem;
    }
    .top-status-detail-value {
        color: var(--ink-strong);
        font-size: 0.98rem;
        line-height: 1.45;
        font-weight: 600;
        word-break: break-word;
    }
    .status-stack {
        display: grid;
        gap: 0.8rem;
    }
    .workflow-card {
        background: linear-gradient(180deg, #fbfcfd 0%, #f5f8fa 100%);
        border: 1px solid #d7e2e9;
        border-radius: 16px;
        padding: 0.95rem 1rem;
    }
    .workflow-card.completed {
        background: #edf8f4;
        border-color: #9bd1c0;
    }
    .workflow-card.rejected {
        background: #fbeef1;
        border-color: #e3a2ae;
    }
    .workflow-card.pending {
        background: #fff8df;
        border-color: #ead27a;
    }
    .workflow-card-label {
        font-size: 0.75rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #6b808f;
        font-weight: 700;
        margin-bottom: 0.35rem;
    }
    .workflow-card-status {
        font-size: 1rem;
        font-weight: 800;
        letter-spacing: -0.02em;
        margin-bottom: 0.3rem;
    }
    .workflow-card-status.completed {
        color: #1d7a60 !important;
    }
    .workflow-card-status.rejected {
        color: #a33b4e !important;
    }
    .workflow-card-status.pending {
        color: #7b5e11 !important;
    }
    .workflow-card-reason {
        color: var(--ink);
        font-size: 0.92rem;
        line-height: 1.5;
        margin: 0;
    }
    .alert-note {
        background: #fff6d7;
        border: 1px solid #eed789;
        color: #6b5407;
        border-radius: 14px;
        padding: 0.8rem 0.9rem;
        font-size: 0.92rem;
        line-height: 1.45;
        font-weight: 600;
    }
    .metric-shell {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 18px;
        padding: 1rem 1.05rem;
        height: 100%;
        box-shadow: 0 10px 24px rgba(16, 37, 51, 0.05);
    }
    .metric-label {
        font-size: 0.76rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #718797;
        font-weight: 700;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        font-size: 1.08rem;
        font-weight: 700;
        color: var(--ink-strong);
        line-height: 1.3;
        word-break: break-word;
    }
    .pill-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
        margin-top: 0.55rem;
    }
    .pill {
        background: #e7eef3;
        color: #204459;
        border-radius: 999px;
        padding: 0.36rem 0.72rem;
        font-size: 0.84rem;
        font-weight: 700;
        border: 1px solid #d2dfe8;
    }
    .section-copy {
        color: var(--ink);
        line-height: 1.55;
        font-size: 1rem;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem;
        margin-bottom: 0.8rem;
    }
    .stTabs [data-baseweb="tab"] {
        background: #eef3f6;
        border-radius: 999px;
        color: var(--ink);
        padding: 0.5rem 0.9rem;
        border: 1px solid #d8e3ea;
        font-weight: 700;
    }
    .stTabs [aria-selected="true"] {
        background: #163247 !important;
        color: #ffffff !important;
        border-color: #163247 !important;
    }
    .stExpander {
        border-radius: 16px !important;
        border: 1px solid #d8e3ea !important;
        background: #ffffff !important;
    }
    @media (max-width: 900px) {
        .main .block-container {
            padding-top: 1.25rem;
        }
        .hero-title {
            max-width: none;
        }
        .top-grid {
            grid-template-columns: 1fr;
        }
        .summary-meta {
            grid-template-columns: 1fr;
        }
        .top-status-details {
            grid-template-columns: 1fr;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def main() -> None:
    """Render the Streamlit invoice workflow app."""

    if "workflow_result" not in st.session_state:
        st.session_state.workflow_result = None
        st.session_state.workflow_error = None
        st.session_state.workflow_source_name = None
        st.session_state.workflow_source_path = None

    content_placeholder = st.empty()

    render_sidebar(content_placeholder)
    if st.session_state.workflow_error is None:
        source_path = Path(st.session_state.workflow_source_path) if st.session_state.workflow_source_path else None
        render_main_content(
            content_placeholder,
            state=st.session_state.workflow_result,
            source_path=source_path,
        )


def render_main_content(content_placeholder, state: AgentState | None = None, source_path: Path | None = None) -> None:
    """Render the main placeholder content for initial, processing, and completed states."""

    with content_placeholder.container():
        render_hero()
        if state is not None and source_path is not None:
            render_results(state, source_path)
        else:
            st.info("Use the sidebar to upload a supported file, then select Process Invoice.")


def render_sidebar(content_placeholder) -> None:
    """Render the sidebar controls and workflow status."""

    with st.sidebar:
        st.markdown(
            """
            <div class="sidebar-panel">
                <div class="sidebar-title">Invoice Intake</div>
                <div class="sidebar-headline">Upload one invoice for review.</div>
                <p class="sidebar-copy">Submit a supported invoice file to review the decision, payment status, and next action.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        source_path = render_input_panel()
        process_clicked = st.button("Process Invoice", type="primary", use_container_width=True)

        if process_clicked:
            if source_path is not None:
                with st.spinner("Running workflow..."):
                    try:
                        result = process_invoice(source_path, content_placeholder)
                    except Exception as exc:
                        title, message = describe_workflow_error(exc)
                        st.session_state.workflow_result = None
                        st.session_state.workflow_source_name = None
                        st.session_state.workflow_source_path = None
                        st.session_state.workflow_error = (title, message, str(exc))
                    else:
                        st.session_state.workflow_result = result
                        st.session_state.workflow_source_name = source_path.name
                        st.session_state.workflow_source_path = str(source_path)
                        st.session_state.workflow_error = None

def render_input_panel() -> Path | None:
    """Render invoice selection controls in the sidebar.

    :return: Selected invoice path, if any.
    """

    st.caption("Supported file types: PDF, TXT, CSV, JSON, XML")

    selected_path: Path | None = None
    uploaded = st.file_uploader(
        "Upload invoice",
        type=["pdf", "txt", "csv", "json", "xml"],
        accept_multiple_files=False,
    )
    if uploaded is not None:
        try:
            selected_path = save_uploaded_invoice(uploaded.name, uploaded.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.caption(f"Ready: {uploaded.name}")

    return selected_path


def render_hero() -> None:
    """Render the main page hero content."""

    st.markdown(
        """
        <div class="app-shell">
            <div class="hero-kicker">Invoice Review Dashboard</div>
            <div class="hero-title">Review invoice decisions in a clear, business-friendly format.</div>
            <p class="hero-copy">
                Upload an invoice to review what was submitted, what decision was made, whether payment can proceed,
                and what action should happen next.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_results(state, source_path: Path) -> None:
    """Render the main workflow results.

    :param state: Final workflow state.
    :param source_path: Source invoice path.
    """

    report = state.final_report
    invoice = state.invoice

    render_top_grid(state, source_path)

    primary_col, secondary_col = st.columns([1.45, 1.0], gap="large")

    with primary_col:
        with st.container():
            st.subheader("Business Summary")
            if report is None:
                st.markdown(
                    '<div class="section-copy">The workflow is still processing. A business summary and next action '
                    "will appear here when review is complete.</div>",
                    unsafe_allow_html=True,
                )
                st.write("**Next action:** Pending workflow completion.")
            else:
                st.markdown(f'<div class="section-copy">{report.summary}</div>', unsafe_allow_html=True)
                st.write(f"**Next action:** {report.next_action}")

    with secondary_col:
        with st.container():
            render_workflow_status_panel(state)

    details_tab, validation_tab, approval_tab, logs_tab = st.tabs(
        ["Invoice Data", "Validation", "Approval & Payment", "Logs"]
    )

    with details_tab:
        st.write(invoice.model_dump() if invoice else {})

    with validation_tab:
        st.write(state.validation.model_dump() if state.validation else {})

    with approval_tab:
        left, right = st.columns(2)
        with left:
            st.markdown("**Approval Detail**")
            st.write(state.approval.model_dump() if state.approval else {})
        with right:
            st.markdown("**Payment Detail**")
            st.write(state.payment.model_dump() if state.payment else {})

    with logs_tab:
        if state.logs:
            for entry in state.logs:
                st.write(format_log_line(entry))
        else:
            st.caption("No logs are available for this workflow run.")


def render_top_grid(state, source_path: Path) -> None:
    """Render the top 40/60 dashboard split.

    :param state: Final workflow state.
    :param source_path: Source invoice path.
    """

    report = state.final_report
    invoice = state.invoice
    amount = f"{invoice.currency} {invoice.amount:,.2f}" if invoice else "Unknown"

    if report is not None:
        decision_label = format_outcome_label(report.outcome)
        decision_copy = _decision_panel_copy(state)
        hero_col, summary_col = st.columns([2, 3], gap="large")
        with summary_col:
            render_top_status_panel(
                title=decision_label,
                body=decision_copy,
                tone=report.outcome,
                invoice_id=invoice.invoice_id if invoice and invoice.invoice_id else "Unknown",
                vendor=invoice.vendor if invoice and invoice.vendor else "Unknown",
                amount=amount,
                source=source_path.name,
            )
        with hero_col:
            render_post_result_context(state)
    elif invoice is not None:
        summary_label = "Processing..."
        summary_copy = "The invoice has been received and is moving through review."
        render_top_status_panel(
            title=summary_label,
            body=summary_copy,
            tone="processing",
            invoice_id=invoice.invoice_id if invoice and invoice.invoice_id else "Unknown",
            vendor=invoice.vendor if invoice and invoice.vendor else "Unknown",
            amount=amount,
            source=source_path.name,
        )
    else:
        summary_label = "Invoice received"
        summary_copy = "The invoice is entering the workflow."
        render_top_status_panel(
            title=summary_label,
            body=summary_copy,
            tone="info",
            invoice_id=invoice.invoice_id if invoice and invoice.invoice_id else "Unknown",
            vendor=invoice.vendor if invoice and invoice.vendor else "Unknown",
            amount=amount,
            source=source_path.name,
        )


def _decision_panel_copy(state: AgentState) -> str:
    """Return concise top-panel copy without duplicating the final summary."""

    report = state.final_report
    if report is None:
        return "The invoice is moving through review. A final decision will appear when processing is complete."

    if report.outcome == "approved_paid":
        return "The invoice was approved and payment completed successfully. Review the business summary for the audit-ready rationale and next action."
    if report.outcome == "rejected":
        return "The invoice was rejected before payment. Review the business summary for the rationale and recommended next action."
    if report.outcome == "payment_failed":
        return "The invoice was approved, but payment did not complete. Review the business summary for retry guidance and follow-up steps."
    return "The workflow reached a final decision. Review the business summary for the audit-ready rationale and next action."


def process_invoice(source_path: Path, content_placeholder):
    """Run the workflow and refresh the main panel as states arrive.

    :param source_path: Source invoice path.
    :param content_placeholder: Placeholder for the main content area.
    :return: Final workflow state.
    """

    latest_state = None
    for current_state in stream_invoice_workflow(str(source_path)):
        latest_state = current_state
        render_main_content(content_placeholder, state=current_state, source_path=source_path)

    if latest_state is None:
        raise RuntimeError("The workflow did not return any state updates.")
    return latest_state


def render_top_status_panel(
    title: str,
    body: str,
    tone: str,
    invoice_id: str,
    vendor: str,
    amount: str,
    source: str,
) -> None:
    """Render the top workflow status section with native Streamlit widgets.

    :param title: Main status title.
    :param body: Supporting status summary.
    :param tone: Outcome or processing tone.
    :param invoice_id: Invoice identifier.
    :param vendor: Vendor name.
    :param amount: Invoice amount.
    :param source: Source file name.
    """

    tone_class = _summary_tone(tone)
    st.markdown(
        f"""
        <div class="top-status-shell">
            <div class="top-status-kicker">Invoice Decision</div>
            <div class="top-status-banner {tone_class}">
                <div class="top-status-banner-title">{title}</div>
                <p class="top-status-banner-copy">{body}</p>
            </div>
            <div class="top-status-details">
                <div>
                    <div class="top-status-detail-label">Invoice</div>
                    <div class="top-status-detail-value">{invoice_id}</div>
                </div>
                <div>
                    <div class="top-status-detail-label">Vendor</div>
                    <div class="top-status-detail-value">{vendor}</div>
                </div>
                <div>
                    <div class="top-status-detail-label">Amount</div>
                    <div class="top-status-detail-value">{amount}</div>
                </div>
                <div>
                    <div class="top-status-detail-label">Source</div>
                    <div class="top-status-detail-value">{source}</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_post_result_context(state: AgentState) -> None:
    """Render a supportive left-side panel after processing completes.

    :param state: Final workflow state.
    """

    report = state.final_report
    st.markdown(
        """
        <div class="app-shell">
            <div class="hero-title">Final decision and next steps are ready.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if report is not None:
        st.write("The invoice has completed review. Use the summary and workflow sections to confirm the decision and next action.")


def render_workflow_status_panel(state: AgentState) -> None:
    """Render detailed workflow status in the right column.

    :param state: Final or in-progress workflow state.
    """

    st.subheader("Workflow Status")

    for stage in stage_overview(state):
        status_label, status_class, reason = _display_stage(stage)
        st.markdown(
            f"""
            <div class="workflow-card {status_class}">
                <div class="workflow-card-label">{stage['label']}</div>
                <div class="workflow-card-status {status_class}">{status_label}</div>
                {f'<p class="workflow-card-reason">{reason}</p>' if reason else ''}
            </div>
            """,
            unsafe_allow_html=True,
        )

    if state.approval is not None and state.approval.requires_scrutiny:
        st.markdown(
            '<div class="alert-note">This invoice required additional scrutiny before approval.</div>',
            unsafe_allow_html=True,
        )


def _summary_tone(outcome: str | None) -> str:
    """Map outcomes to summary tone classes.

    :param outcome: Final outcome code.
    :return: CSS tone class.
    """

    if outcome == "rejected":
        return "warning"
    if outcome == "approved_paid":
        return "success"
    if outcome == "payment_failed":
        return "error"
    if outcome == "processing":
        return "processing"
    return "info"


def _display_stage(stage: dict[str, str]) -> tuple[str, str, str | None]:
    """Map internal stage state to display status and reason.

    :param stage: Stage summary from presentation helpers.
    :return: Status label, CSS class, and optional reason.
    """

    status = stage["status"]
    next_step = stage.get("next")
    reason = stage["reason"]
    if next_step == "reject" or "was skipped because the invoice was not approved" in reason.lower():
        return ("Rejected", "rejected", reason)
    if status == "completed":
        return ("Completed", "completed", None)
    if status in {"failed", "skipped"}:
        return ("Rejected", "rejected", reason)
    return ("Processing...", "pending", reason)


if __name__ == "__main__":
    main()
