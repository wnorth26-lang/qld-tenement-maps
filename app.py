#!/usr/bin/env python3
"""
app.py - Streamlit prototype for a self-serve QLD tenement map generator.

Wraps epm_locality_map.py (the locality-map / sub-block-report engine) in a
simple web form: pick a preset/house style, pick a map type, type in an EPM
number, and get a PDF/PNG back - either as a download or emailed to you.

This is a scoping prototype, not a production build. It demonstrates the UX
pattern (dropdowns + EPM number -> generated map) and the plumbing needed to
get there (background-style generation, PDF+PNG export, optional email
delivery). Things intentionally left as "next steps" rather than built out
here are called out in comments: job queueing for scale, accounts/saved
presets, caching the sub-block grid, and support for tenure types other than
EPMs (which don't use the sub-block system).

Run with:
    pip install -r requirements.txt
    streamlit run app.py
"""

import io
import os
import tempfile
from datetime import datetime

import streamlit as st

import epm_locality_map as m

st.set_page_config(page_title="QLD Tenement Map Generator", layout="wide")

# ---------------------------------------------------------------------------
# Presets: in a real product these would be saved per-client (their logo,
# house colours, standard footer wording, default basemap) rather than a
# hardcoded dict - this shows the shape that system would take. Deeper visual
# customisation (colours/fonts) would need those to be parameters in
# epm_locality_map.py too, which today are mostly fixed - a natural next step
# once you know which knobs clients actually want to turn.
# ---------------------------------------------------------------------------
PRESETS = {
    "Default": {
        "company_name": "",  # blank = auto-pulled from the EPM's registered holder
        "drawn_by": "",
        "report_title_template": "Partial Relinquishment Report {year}",
    },
    "Annual report style": {
        "company_name": "",
        "drawn_by": "",
        "report_title_template": "Annual Report {year}",
    },
    "Example client house style (Ravenswood Gold)": {
        "company_name": "RAVENSWOOD GOLD",
        "drawn_by": "RP",
        "report_title_template": "Partial Relinquishment Report {year}",
    },
}

MAP_TYPES = [
    "Locality map (APO / permit application)",
    "Sub-block plan (annual report)",
    "Partial relinquishment report",
]


def try_send_email(to_addr, subject, body, attachments):
    """attachments: list of (filename, bytes, maintype, subtype).
    Looks for SMTP credentials in .streamlit/secrets.toml under [smtp]:
        host, port, user, password, from (optional)
    Not configured in this prototype by default - falls back to a clear
    message telling you what to add rather than failing silently."""
    import smtplib
    import ssl
    from email.message import EmailMessage

    try:
        smtp_host = st.secrets["smtp"]["host"]
        smtp_port = int(st.secrets["smtp"]["port"])
        smtp_user = st.secrets["smtp"]["user"]
        smtp_pass = st.secrets["smtp"]["password"]
        from_addr = st.secrets["smtp"].get("from", smtp_user)
    except Exception:
        return False, ("Email isn't configured in this prototype. Add SMTP credentials to "
                        ".streamlit/secrets.toml under a [smtp] section (host, port, user, "
                        "password) to enable sending - see the comment in try_send_email().")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(body)
    for fname, data, maintype, subtype in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True, f"Emailed to {to_addr}."
    except Exception as e:
        return False, f"Email send failed: {e}"


st.title("QLD Tenement Map Generator")
st.caption("Prototype - wraps the EPM locality-map / sub-block-report script in a self-serve form.")

with st.sidebar:
    st.header("1. Preset / house style")
    preset_name = st.selectbox("Preset", list(PRESETS.keys()))
    preset = PRESETS[preset_name]

    st.header("2. Map type")
    map_type = st.selectbox("What do you need?", MAP_TYPES)

    st.header("3. Basemap")
    if map_type == MAP_TYPES[0]:
        basemap = st.selectbox("Background", ["satellite", "none"])
    else:
        basemap_choice = st.selectbox(
            "Background",
            ["Both (satellite + greyscale)", "Satellite only", "Greyscale only", "None"],
        )
        basemap = {
            "Both (satellite + greyscale)": "both",
            "Satellite only": "satellite",
            "Greyscale only": "greyscale",
            "None": "none",
        }[basemap_choice]

    st.header("4. Extra context layers")
    st.caption(
        "Pulled live from the same public QLD spatial-gis services GeoResGlobe itself runs "
        "on. This is a curated subset, not literally every layer GeoResGlobe has - most of "
        "the rest are either the wrong scale to be useful on a tenement map, or (like Native "
        "Title/Cultural Heritage and National Parks/State Forest layers) gated behind an "
        "API token this prototype doesn't have. More layers can be added to LAYER_CATALOG "
        "in epm_locality_map.py the same way these were."
    )
    extra_layer_labels = {v["label"]: k for k, v in m.LAYER_CATALOG.items()}
    selected_labels = st.multiselect("Layers to add to the map", list(extra_layer_labels.keys()))
    extra_layers = [extra_layer_labels[lbl] for lbl in selected_labels]

st.header("Tenement")
source = st.radio(
    "Data source",
    ["Look up EPM number (live government data)", "Upload a local GeoJSON file"],
    horizontal=True,
)

epm_number = None
uploaded = None
if source.startswith("Look up"):
    epm_number = st.text_input("EPM number", placeholder="e.g. EPM 25210")
else:
    uploaded = st.file_uploader("GeoJSON with a single EPM feature", type=["geojson", "json"])

col1, col2 = st.columns(2)
with col1:
    author = st.text_input("Author", value="Will North")
    project_name = st.text_input("Project name (optional - defaults to the permit name)")
    drawn_by = st.text_input("Drawn-by initials (optional)", value=preset["drawn_by"])
with col2:
    company_name = st.text_input(
        "Company name (optional - auto-pulled from the EPM's holder if left blank)",
        value=preset["company_name"],
    )
    page_number = st.text_input("Page number (optional)")
    forced_scale = st.text_input("Force scale denominator (optional, e.g. 100000)")

relinquish_codes, report_title = [], None
if map_type == MAP_TYPES[2]:
    relinquish_raw = st.text_input(
        "Sub-block codes being relinquished (comma-separated)",
        placeholder="e.g. 291J,291O,291T,291Y",
    )
    relinquish_codes = [c.strip() for c in relinquish_raw.split(",") if c.strip()]
    default_title = preset["report_title_template"].format(year=datetime.now().year)
    report_title = st.text_input("Report title", value=default_title)
elif map_type == MAP_TYPES[1]:
    default_title = "Annual Report {year}".format(year=datetime.now().year)
    report_title = st.text_input("Report title", value=default_title)

st.divider()
email_to = st.text_input("Email address to send the finished map(s) to (optional)")
generate = st.button("Generate map", type="primary")

if generate:
    st.session_state.pop("results", None)
    with st.spinner("Fetching tenement data and building the map - this can take a little while..."):
        try:
            if source.startswith("Look up"):
                if not epm_number or not epm_number.strip():
                    st.error("Enter an EPM number first.")
                    st.stop()
                gdf = m.fetch_epm_gdf(epm_number)
            else:
                if uploaded is None:
                    st.error("Upload a GeoJSON file first.")
                    st.stop()
                suffix = os.path.splitext(uploaded.name)[1] or ".geojson"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(uploaded.getbuffer())
                    tmp_path = tmp.name
                gdf = m.load_local_gdf(tmp_path)

            scale_val = int(forced_scale) if forced_scale.strip() else None
            results = []  # list of (label, fig)

            if map_type == MAP_TYPES[0]:
                fig, scale, zone, crs, legend_fig = m.build_map(gdf, author, scale_val, basemap,
                                                                  extra_layers=extra_layers)
                results.append(("Locality map", fig))
                if legend_fig is not None:
                    results.append(("Locality map - Legend (moved to its own page - "
                                     "too many entries to fit on the map)", legend_fig))
            else:
                basemaps_to_run = ["satellite", "greyscale"] if basemap == "both" else [basemap]
                for bm in basemaps_to_run:
                    figs, subblocks, zone = m.build_subblock_maps(
                        gdf,
                        author,
                        relinquish_codes=relinquish_codes or None,
                        forced_scale=scale_val,
                        project_name=project_name or None,
                        drawn_by=drawn_by or None,
                        report_title=report_title,
                        page_number=page_number or None,
                        company_name=company_name or None,
                        basemap=bm,
                        extra_layers=extra_layers,
                    )
                    results.append((f"Sub-block map ({bm})", figs["map"]))
                    if figs.get("legend") is not None:
                        results.append((f"Sub-block map ({bm}) - Legend (moved to its own page - "
                                         "too many entries to fit on the map)", figs["legend"]))

            st.session_state["results"] = results
            st.success(f"Generated {len(results)} map(s).")
        except Exception as e:
            st.error(f"Couldn't generate the map: {e}")
            st.stop()

if "results" in st.session_state:
    attachments = []
    for label, fig in st.session_state["results"]:
        st.subheader(label)
        st.pyplot(fig)

        pdf_buf = io.BytesIO()
        fig.savefig(pdf_buf, format="pdf", dpi=300)
        png_buf = io.BytesIO()
        fig.savefig(png_buf, format="png", dpi=200)

        safe_label = label.replace(" ", "_").replace("(", "").replace(")", "")
        c1, c2 = st.columns(2)
        with c1:
            st.download_button(f"Download {label} - PDF", pdf_buf.getvalue(),
                                file_name=f"{safe_label}.pdf", mime="application/pdf",
                                key=f"pdf_{safe_label}")
        with c2:
            st.download_button(f"Download {label} - PNG", png_buf.getvalue(),
                                file_name=f"{safe_label}.png", mime="image/png",
                                key=f"png_{safe_label}")

        attachments.append((f"{safe_label}.pdf", pdf_buf.getvalue(), "application", "pdf"))

    if email_to:
        if st.button("Send email now"):
            ok, message = try_send_email(
                email_to,
                subject=f"Your tenement map(s) - {map_type}",
                body="Attached are the maps you generated. Automatically produced - please "
                     "verify boundary/sub-block details against the source tenement record "
                     "before lodging.",
                attachments=attachments,
            )
            (st.success if ok else st.warning)(message)
