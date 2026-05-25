"""
Lumira Intelligence Pipeline — Streamlit Testing Dashboard

Run:  streamlit run dashboard.py
      (API must be running: make api  OR  make start)
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import math
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ── Config ────────────────────────────────────────────────────────────────
API_BASE = "http://localhost:8000"
TIMEOUT  = 30   # seconds

st.set_page_config(
    page_title="Lumira Dashboard",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Colour maps ───────────────────────────────────────────────────────────
EVENT_COLOURS = {
    "VIOLENCE":       "#e74c3c",
    "TERRORISM":      "#c0392b",
    "PROTEST":        "#e67e22",
    "DISASTER":       "#9b59b6",
    "ACCIDENT":       "#f39c12",
    "CRIME":          "#d35400",
    "MILITARY":       "#2c3e50",
    "POLITICAL":      "#2980b9",
    "HEALTH":         "#27ae60",
    "INFRASTRUCTURE": "#7f8c8d",
    "UNKNOWN":        "#95a5a6",
}

SEVERITY_LABELS = {1: "⬜ 1 – Minimal", 2: "🟡 2 – Low",
                   3: "🟠 3 – Moderate", 4: "🔴 4 – High", 5: "🆘 5 – Critical"}

def dti_colour(score: float) -> str:
    if score >= 75: return "#e74c3c"
    if score >= 50: return "#e67e22"
    if score >= 25: return "#f1c40f"
    return "#2ecc71"


# ── Globe map helpers (Plotly orthographic projection) ────────────────────
# True 3D sphere — draggable, no API key needed, Plotly already installed.
# Uses orthographic projection which renders the Earth as a genuine sphere
# just like Google Earth, with full rotation via mouse drag.

def _globe_layout(center_lon: float = 82.0, center_lat: float = 22.0,
                  height: int = 580) -> dict:
    """
    Shared Plotly layout for both the Events and Assets globe maps.
    Orthographic projection = true sphere.  Country outlines + lat/lon grid
    give the 'wireframe of Earth' look on a near-black background.
    """
    return dict(
        geo=dict(
            projection_type="orthographic",
            projection=dict(
                rotation=dict(lon=center_lon, lat=center_lat, roll=0)
            ),
            # ── Land / ocean colours ──────────────────────────────────────
            showland=True,
            landcolor="rgb(12, 22, 38)",        # very dark navy — sphere visible
            showocean=True,
            oceancolor="rgb(5, 10, 22)",         # slightly different dark
            showlakes=True,
            lakecolor="rgb(5, 10, 22)",
            # ── Wireframe outlines ────────────────────────────────────────
            showcountries=True,
            countrycolor="rgba(0, 200, 255, 0.50)",   # cyan country borders
            countrywidth=0.7,
            showcoastlines=True,
            coastlinecolor="rgba(0, 230, 255, 0.70)",  # brighter coastlines
            coastlinewidth=0.9,
            showframe=False,
            bgcolor="rgb(0, 0, 0)",                    # black space background
            # ── Lat / lon grid (adds to the 'wireframe' feel) ────────────
            lataxis=dict(
                showgrid=True,
                gridcolor="rgba(0, 130, 190, 0.20)",
                dtick=30,
            ),
            lonaxis=dict(
                showgrid=True,
                gridcolor="rgba(0, 130, 190, 0.20)",
                dtick=30,
            ),
        ),
        paper_bgcolor="rgb(0, 5, 15)",
        plot_bgcolor="rgb(0, 5, 15)",
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(
            bgcolor="rgba(0,5,15,0.7)",
            font=dict(color="white", size=11),
            bordercolor="rgba(0,200,255,0.3)",
            borderwidth=1,
        ),
    )


# ── OSM map helpers (Plotly go.Scattermap — OpenStreetMap, no API key) ────
def _osm_layout(lat: float = 22.0, lon: float = 82.0,
                zoom: int = 4, height: int = 540) -> dict:
    """Shared layout for go.Scattermap with full OpenStreetMap tile detail."""
    return dict(
        map=dict(
            style="open-street-map",      # free OSM tiles, roads/buildings/places
            center=dict(lat=lat, lon=lon),
            zoom=zoom,
        ),
        height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(
            bgcolor="rgba(255,255,255,0.85)",
            font=dict(size=11),
            bordercolor="#ccc",
            borderwidth=1,
        ),
        paper_bgcolor="#0d1117",
    )


def _circle_coords(lat: float, lon: float, radius_m: float, n: int = 72):
    """Generate (lats, lons) tracing a geodesic circle — used for alert rings."""
    R = 6_371_000.0
    lats, lons = [], []
    for i in range(n + 1):
        angle = math.radians(i * 360 / n)
        dlat = math.degrees(radius_m / R * math.cos(angle))
        dlon = math.degrees(
            radius_m / (R * math.cos(math.radians(lat))) * math.sin(angle)
        )
        lats.append(lat + dlat)
        lons.append(lon + dlon)
    return lats, lons


# ── API helpers ───────────────────────────────────────────────────────────
def api(method: str, path: str, **kwargs) -> Optional[Any]:
    try:
        resp = requests.request(method, f"{API_BASE}{path}",
                                timeout=TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("❌  Cannot reach API at `http://localhost:8000` — run `make api` first.")
        return None
    except Exception as e:
        st.error(f"API error: {e}")
        return None


def get(path: str, **params) -> Optional[Any]:
    return api("GET", path, params=params)

def post(path: str, json=None, files=None, data=None) -> Optional[Any]:
    return api("POST", path, json=json, files=files, data=data)


# ── Sidebar navigation ────────────────────────────────────────────────────
PAGES = [
    "🏠  System Health",
    "🔬  Text Pipeline",
    "🎙️  Audio Pipeline",
    "🖼️  Image Pipeline",
    "📡  Ingestion Control",
    "📋  Events Browser",
    "🗺️  Threat Index (DTI)",
    "🚨  Proximity Alerts",
    "🏢  Assets",
]

st.sidebar.image(
    "https://img.icons8.com/color/96/shield.png", width=64
)
st.sidebar.title("Lumira")
st.sidebar.caption("Intelligence Pipeline")
st.sidebar.divider()
page = st.sidebar.radio("Navigate", PAGES, label_visibility="collapsed")
st.sidebar.divider()
st.sidebar.caption(f"API: `{API_BASE}`")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 1 — System Health
# ══════════════════════════════════════════════════════════════════════════
if page == PAGES[0]:
    st.title("🏠 System Health")

    status = get("/pipeline/status")

    if status:
        # ── Service indicators ────────────────────────────────────────────
        st.subheader("Services")
        cols = st.columns(6)
        checks = {
            "PostgreSQL":   status.get("postgres"),
            "Redis":        status.get("redis"),
            "OpenSearch":   status.get("opensearch"),
            "MinIO":        status.get("minio"),
            "Ollama (LLM)": status.get("ollama_text_model"),
        }
        for col, (name, ok) in zip(cols, checks.items()):
            icon  = "✅" if ok else "❌"
            color = "#2ecc71" if ok else "#e74c3c"
            col.markdown(
                f"""<div style='text-align:center;padding:12px;border-radius:8px;
                background:{color}22;border:1px solid {color}'>
                <div style='font-size:1.6rem'>{icon}</div>
                <div style='font-size:.85rem;font-weight:600'>{name}</div>
                </div>""",
                unsafe_allow_html=True,
            )

        # ── Metrics ───────────────────────────────────────────────────────
        st.divider()
        st.subheader("Live Metrics")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Events",      f"{status.get('total_events', 0):,}")
        m2.metric("Events (last 24h)", f"{status.get('events_last_24h', 0):,}")
        m3.metric("Active Alerts",     f"{status.get('active_alerts', 0):,}")
        m4.metric("Queue Depth",       f"{status.get('queue_depth', 0):,}")

    # ── Recent events ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("Recent Events (last 10)")
    data = get("/events", hours=24, limit=10)
    if data and data.get("items"):
        rows = []
        for e in data["items"]:
            rows.append({
                "Time":        e.get("ingested_at", "")[:19].replace("T", " "),
                "Source":      e.get("source", ""),
                "Type":        e.get("event_type", ""),
                "Sev":         e.get("severity", ""),
                "District":    e.get("district", "—"),
                "State":       e.get("state", "—"),
                "Title":       (e.get("title") or e.get("description") or "")[:80],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No events yet — trigger ingestion to get started.")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 2 — Text Pipeline Tester
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[1]:
    st.title("🔬 Text Pipeline Tester")
    st.caption("Sends text through Qwen3.5:4b → NER + event classification + geocoding")

    SAMPLES = {
        "Custom…": "",
        "Armed clash J&K":
            "Firing exchange reported between security forces and militants near the Line of Control "
            "in Kupwara district, Jammu and Kashmir. Two soldiers injured.",
        "Delhi protest":
            "Thousands of farmers gathered at Jantar Mantar in New Delhi today demanding MSP "
            "guarantee. Police deployed heavy security.",
        "Mumbai flood":
            "Heavy monsoon rains triggered severe flooding across low-lying areas of Dharavi and "
            "Kurla in Mumbai. Several families displaced, NDRF teams deployed.",
        "Coimbatore blast":
            "An IED explosion near a temple in Coimbatore, Tamil Nadu injured three people. "
            "The NIA has taken over the investigation.",
    }

    sample = st.selectbox("Load sample", list(SAMPLES.keys()))
    text_input = st.text_area(
        "Text to analyse",
        value=SAMPLES[sample],
        height=140,
        placeholder="Paste a news snippet, RSS item, or any text about an event in India…",
    )

    if st.button("▶  Analyse", type="primary", disabled=not text_input.strip()):
        with st.spinner("Calling Qwen3.5:4b…"):
            result = post("/pipeline/test/text", json={"text": text_input})

        if result and "error" not in result:
            # ── Event type badge ──────────────────────────────────────────
            et    = result.get("event_type", "UNKNOWN")
            color = EVENT_COLOURS.get(et, "#95a5a6")
            sev   = result.get("severity", 1)
            conf  = result.get("confidence", 0)

            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(
                    f"""<span style='background:{color};color:white;padding:4px 14px;
                    border-radius:20px;font-weight:700;font-size:1rem'>{et}</span>""",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Description:** {result.get('description', '')}")
            with col2:
                st.metric("Severity", SEVERITY_LABELS.get(sev, sev))
                st.metric("Confidence", f"{conf:.0%}")

            # ── Locations ─────────────────────────────────────────────────
            locs = result.get("locations", [])
            geo  = result.get("geocoded")
            if locs:
                st.subheader("📍 Locations extracted")
                loc_cols = st.columns(min(len(locs), 4))
                for col, loc in zip(loc_cols, locs):
                    col.markdown(
                        f"""<div style='border:1px solid #ddd;border-radius:6px;
                        padding:8px;text-align:center'>
                        <b>{loc['name']}</b><br>
                        <small>{loc['entity_type']}</small></div>""",
                        unsafe_allow_html=True,
                    )
                if geo:
                    st.success(
                        f"✅ Geocoded → **{geo['resolved_name']}**  "
                        f"| District: {geo.get('district','—')}  "
                        f"| State: {geo.get('state','—')}  "
                        f"| `{geo.get('latitude'):.4f}, {geo.get('longitude'):.4f}`"
                    )
                    # Mini map
                    df_map = pd.DataFrame([{"lat": geo["latitude"], "lon": geo["longitude"]}])
                    st.map(df_map, zoom=7)

            # ── Entities ──────────────────────────────────────────────────
            ents = result.get("entities", {})
            if any(ents.get(k) for k in ("people", "organizations", "keywords")):
                st.subheader("🏷️ Entities")
                e1, e2, e3 = st.columns(3)
                with e1:
                    st.markdown("**People**")
                    for p in ents.get("people", []) or ["—"]:
                        st.markdown(f"- {p}")
                with e2:
                    st.markdown("**Organizations**")
                    for o in ents.get("organizations", []) or ["—"]:
                        st.markdown(f"- {o}")
                with e3:
                    st.markdown("**Keywords**")
                    kws = " ".join(
                        f"`{k}`" for k in (ents.get("keywords") or [])[:10]
                    )
                    st.markdown(kws or "—")
        elif result:
            st.error(f"Pipeline error: {result.get('error')}")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 3 — Audio Pipeline Tester
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[2]:
    st.title("🎙️ Audio Pipeline Tester")
    st.caption("Upload an audio clip → Whisper transcription → text analysis")

    uploaded = st.file_uploader(
        "Upload audio file", type=["mp3", "wav", "ogg", "m4a", "flac"]
    )

    if uploaded:
        st.audio(uploaded)

        if st.button("▶  Transcribe & Analyse", type="primary"):
            with st.spinner("Running Whisper + Qwen3.5:4b…"):
                result = post(
                    "/pipeline/test/audio",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                )

            if result and "error" not in result:
                st.subheader("📝 Transcript")
                transcript = result.get("transcript", "")
                st.info(transcript or "_(no speech detected)_")

                c1, c2, c3 = st.columns(3)
                c1.metric("Language",    result.get("language", "—").upper())
                c2.metric("Lang. Prob.", f"{result.get('language_probability', 0):.0%}")
                c3.metric("Duration",    f"{result.get('duration_seconds', 0):.1f}s")

                analysis = result.get("analysis")
                if analysis:
                    st.divider()
                    st.subheader("🔎 Event Analysis")
                    et    = analysis.get("event_type", "UNKNOWN")
                    color = EVENT_COLOURS.get(et, "#95a5a6")
                    st.markdown(
                        f"""<span style='background:{color};color:white;
                        padding:4px 14px;border-radius:20px;font-weight:700'>{et}</span>""",
                        unsafe_allow_html=True,
                    )
                    col1, col2 = st.columns(2)
                    col1.metric("Severity",   SEVERITY_LABELS.get(analysis.get("severity", 1)))
                    col2.metric("Confidence", f"{analysis.get('confidence', 0):.0%}")
                    st.markdown(f"**Summary:** {analysis.get('description', '')}")
            elif result:
                st.error(result.get("error"))
    else:
        st.info("Upload an MP3, WAV, or OGG file to test the Whisper pipeline.")
        with st.expander("💡 How to get a test audio file"):
            st.markdown("""
- Download any news radio clip (All India Radio, etc.)
- Record yourself reading a news headline
- Use `ffmpeg` to trim: `ffmpeg -i input.mp3 -t 30 test_clip.mp3`
""")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 4 — Image Pipeline Tester
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[3]:
    st.title("🖼️ Image Pipeline Tester")
    st.caption("Upload an image → Qwen3-VL-4B caption + PaddleOCR → text analysis")

    uploaded = st.file_uploader("Upload image", type=["jpg", "jpeg", "png", "webp"])

    if uploaded:
        st.image(uploaded, caption=uploaded.name, width=500)

        if st.button("▶  Analyse Image", type="primary"):
            with st.spinner("Running Qwen3-VL + PaddleOCR…"):
                result = post(
                    "/pipeline/test/image",
                    files={"file": (uploaded.name, uploaded.getvalue(), uploaded.type)},
                )

            if result and "error" not in result:
                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("👁️ Vision Caption")
                    st.write(result.get("caption") or "_No caption generated_")

                with col2:
                    st.subheader("🔡 OCR Text")
                    ocr = result.get("ocr_text", "")
                    st.code(ocr if ocr else "(no text found in image)", language=None)

                analysis = result.get("analysis")
                if analysis:
                    st.divider()
                    st.subheader("🔎 Event Analysis")
                    et    = analysis.get("event_type", "UNKNOWN")
                    color = EVENT_COLOURS.get(et, "#95a5a6")
                    st.markdown(
                        f"""<span style='background:{color};color:white;
                        padding:4px 14px;border-radius:20px;font-weight:700'>{et}</span>""",
                        unsafe_allow_html=True,
                    )
                    a1, a2 = st.columns(2)
                    a1.metric("Severity",   SEVERITY_LABELS.get(analysis.get("severity", 1)))
                    a2.metric("Confidence", f"{analysis.get('confidence', 0):.0%}")
                    st.markdown(f"**Summary:** {analysis.get('description', '')}")
            elif result:
                st.error(result.get("error"))
    else:
        st.info("Upload a JPG or PNG to test the vision + OCR pipeline.")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 5 — Ingestion Control
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[4]:
    st.title("📡 Ingestion Control")

    # ── Manual triggers ───────────────────────────────────────────────────
    st.subheader("Trigger Ingesters")
    st.caption("Fires Celery tasks immediately (worker must be running)")

    ingest_map = {
        "RSS Feeds":  "rss",
        "NewsAPI":    "newsapi",
        "Serper":     "serper",
        "GDELT":      "gdelt",
        "All at once": None,
    }

    cols = st.columns(len(ingest_map))
    for col, (label, key) in zip(cols, ingest_map.items()):
        with col:
            if st.button(f"▶ {label}", use_container_width=True):
                if key:
                    result = post(f"/pipeline/ingest/trigger")
                    if result:
                        st.success(f"Queued: `{result['triggered'].get(key, '—')}`")
                else:
                    result = post("/pipeline/ingest/trigger")
                    if result:
                        st.success("All ingesters queued!")
                        st.json(result)

    # ── Queue depth ───────────────────────────────────────────────────────
    st.divider()
    status = get("/pipeline/status")
    if status:
        q = status.get("queue_depth", 0)
        st.metric("Processing Queue Depth", q,
                  help="Raw items waiting to be processed by the Celery worker")
        if q > 20:
            st.warning(f"{q} items queued — worker may be busy or not running.")

    # ── Recent raw ingestions ─────────────────────────────────────────────
    st.divider()
    st.subheader("Recent Events by Source")
    data = get("/events", hours=24, limit=200)
    if data and data.get("items"):
        df = pd.DataFrame(data["items"])
        if "source" in df.columns:
            source_counts = df["source"].value_counts().reset_index()
            source_counts.columns = ["Source", "Count"]
            fig = px.bar(
                source_counts, x="Source", y="Count",
                color="Source", title="Events ingested in last 24h by source",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(showlegend=False, height=300)
            st.plotly_chart(fig, use_container_width=True)

        if "event_type" in df.columns:
            type_counts = df["event_type"].value_counts().reset_index()
            type_counts.columns = ["Event Type", "Count"]
            fig2 = px.pie(
                type_counts, names="Event Type", values="Count",
                title="Event type distribution",
                color="Event Type",
                color_discrete_map=EVENT_COLOURS,
            )
            fig2.update_layout(height=300)
            st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No events found in the last 24h.")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 6 — Events Browser
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[5]:
    st.title("📋 Events Browser")

    # ── Filters ───────────────────────────────────────────────────────────
    with st.expander("🔍 Filters", expanded=True):
        f1, f2, f3, f4, f5 = st.columns(5)
        f_hours    = f1.selectbox("Time window", [1, 6, 24, 48, 168], index=2,
                                  format_func=lambda h: f"Last {h}h")
        f_type     = f2.selectbox("Event type", ["All"] + list(EVENT_COLOURS.keys()))
        f_sev      = f3.selectbox("Min severity", [1, 2, 3, 4, 5], index=0)
        f_source   = f4.selectbox("Source", ["All", "RSS", "NewsAPI", "Serper", "GDELT", "Radio", "Image", "Video"])
        f_dupes    = f5.checkbox("Include duplicates", value=False)

    params: Dict[str, Any] = {
        "hours":              f_hours,
        "min_severity":       f_sev,
        "include_duplicates": f_dupes,
        "limit":              200,
    }
    if f_type != "All":
        params["event_type"] = f_type
    if f_source != "All":
        params["source"] = f_source

    data = get("/events", **params)
    events = data.get("items", []) if data else []

    st.caption(f"**{len(events)}** events matching filters")

    if not events:
        st.info("No events. Try widening the time window or trigger ingestion.")
        st.stop()

    # ── Map ───────────────────────────────────────────────────────────────
    geo_events = [e for e in events if e.get("latitude") and e.get("longitude")]
    if geo_events:
        st.subheader(f"🗺️ Map ({len(geo_events)} geocoded events)")
        df_map = pd.DataFrame([
            {
                "lat":   e["latitude"],
                "lon":   e["longitude"],
                "type":  e.get("event_type", "UNKNOWN"),
                "title": (e.get("title") or "")[:60],
                "sev":   e.get("severity", 1),
            }
            for e in geo_events
        ])
        fig_events = go.Figure()
        for evt_type, hex_color in EVENT_COLOURS.items():
            subset = df_map[df_map["type"] == evt_type]
            if subset.empty:
                continue
            fig_events.add_trace(go.Scattermap(
                lat=subset["lat"],
                lon=subset["lon"],
                mode="markers",
                name=evt_type,
                marker=dict(
                    size=subset["sev"] * 4 + 6,   # 10–26 px by severity
                    color=hex_color,
                    opacity=0.90,
                ),
                text=[
                    f"<b>{r['title']}</b><br>{r['type']} | Severity {r['sev']}"
                    for _, r in subset.iterrows()
                ],
                hoverinfo="text",
            ))
        fig_events.update_layout(**_osm_layout(lat=22.0, lon=82.0, zoom=4))
        st.plotly_chart(fig_events, use_container_width=True)

    # ── Table ─────────────────────────────────────────────────────────────
    st.subheader("Events Table")
    rows = []
    for e in events:
        et    = e.get("event_type", "UNKNOWN")
        color = EVENT_COLOURS.get(et, "#95a5a6")
        rows.append({
            "Time":       e.get("ingested_at", "")[:19].replace("T", " "),
            "Type":       et,
            "Sev":        e.get("severity", "—"),
            "Conf":       f"{e.get('confidence', 0):.0%}" if e.get("confidence") else "—",
            "District":   e.get("district", "—"),
            "State":      e.get("state", "—"),
            "Source":     e.get("source", "—"),
            "Title":      (e.get("title") or e.get("description") or "")[:90],
        })

    df_events = pd.DataFrame(rows)
    st.dataframe(df_events, use_container_width=True, hide_index=True, height=420)

    # ── Event detail ──────────────────────────────────────────────────────
    st.divider()
    st.subheader("Event Detail")
    event_titles = {
        f"{e.get('ingested_at','')[:16]} — {e.get('event_type','')} — "
        f"{(e.get('title') or '')[:50]}": e["id"]
        for e in events[:50]
    }
    selected = st.selectbox("Select event", list(event_titles.keys()))
    if selected:
        eid    = event_titles[selected]
        detail = get(f"/events/{eid}")
        if detail:
            d1, d2, d3 = st.columns(3)
            et    = detail.get("event_type", "UNKNOWN")
            color = EVENT_COLOURS.get(et, "#95a5a6")
            d1.markdown(
                f"""<span style='background:{color};color:white;padding:4px 12px;
                border-radius:20px;font-weight:700'>{et}</span>""",
                unsafe_allow_html=True,
            )
            d2.metric("Severity",   SEVERITY_LABELS.get(detail.get("severity", 1)))
            d3.metric("Confidence", f"{detail.get('confidence', 0):.0%}")
            st.markdown(f"**Summary:** {detail.get('description', '—')}")
            st.markdown(f"**Source:** [{detail.get('source_url','—')}]({detail.get('source_url','')})")
            st.markdown(f"**Location:** {detail.get('location_name','—')} | "
                        f"District: {detail.get('district','—')} | State: {detail.get('state','—')}")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 7 — Threat Index (DTI)
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[6]:
    st.title("🗺️ District Threat Index")

    col_refresh, col_trigger = st.columns([4, 1])
    with col_trigger:
        if st.button("🔄 Recalculate DTI", type="primary"):
            r = post("/threats/dti/trigger")
            if r:
                st.success(f"DTI task queued: `{r.get('task_id','')[:8]}…`")

    # ── DTI scores ────────────────────────────────────────────────────────
    dti_data = get("/threats/dti", min_score=0, limit=200)
    if not dti_data:
        st.info("No DTI data yet — trigger ingestion + wait for DTI update.")
        st.stop()

    df_dti = pd.DataFrame(dti_data)
    if df_dti.empty:
        st.info("No district scores computed yet.")
        st.stop()

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Districts monitored", len(df_dti))
    m2.metric("Avg DTI score",       f"{df_dti['dti_score'].mean():.1f}")
    m3.metric("Max DTI score",       f"{df_dti['dti_score'].max():.1f}")
    m4.metric("Anomalies detected",  int(df_dti["is_anomaly"].sum()))

    st.divider()

    # ── Bar chart — top 20 ────────────────────────────────────────────────
    top20 = df_dti.nlargest(20, "dti_score")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=top20["district_name"],
        y=top20["dti_score"],
        marker_color=[dti_colour(s) for s in top20["dti_score"]],
        text=top20["dti_score"].round(1),
        textposition="outside",
    ))
    fig.update_layout(
        title="Top 20 Districts by DTI Score",
        xaxis_tickangle=-40,
        yaxis_range=[0, 105],
        height=400,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

    # ── Full table ────────────────────────────────────────────────────────
    st.subheader("All Districts")
    display_cols = ["district_name", "state", "dti_score", "event_count_24h",
                    "avg_severity", "velocity_score", "is_anomaly", "computed_at"]
    available = [c for c in display_cols if c in df_dti.columns]
    df_show = df_dti[available].sort_values("dti_score", ascending=False)
    df_show["computed_at"] = pd.to_datetime(df_show.get("computed_at", "")).dt.strftime("%H:%M:%S")

    def colour_dti(val):
        if isinstance(val, float):
            c = dti_colour(val)
            return f"background-color: {c}33"
        return ""

    st.dataframe(
        df_show.style.map(colour_dti, subset=["dti_score"]),
        use_container_width=True,
        hide_index=True,
        height=420,
    )

    # ── Anomalies ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("🚧 Anomaly Records (last 24h)")
    anomalies = get("/threats/anomalies", hours=24)
    if anomalies:
        st.dataframe(pd.DataFrame(anomalies), use_container_width=True, hide_index=True)
    else:
        st.success("No anomalies detected in the last 24h.")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 8 — Proximity Alerts
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[7]:
    st.title("🚨 Proximity Alerts")

    tab_active, tab_all = st.tabs(["🔴 Active (unacknowledged)", "📜 All Alerts"])

    with tab_active:
        alerts = get("/alerts", acknowledged=False, limit=100)
        if not alerts:
            st.success("✅  No active alerts.")
        else:
            st.warning(f"{len(alerts)} unacknowledged alert(s)")

            for alert in alerts:
                sev   = alert.get("severity", 1)
                color = ["#95a5a6","#f1c40f","#e67e22","#e74c3c","#c0392b"][min(sev,5)-1]
                with st.container():
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1, 1])
                    c1.markdown(f"**{alert.get('asset_name','—')}** _{alert.get('asset_type','')}_")
                    c2.markdown(f"Event `{str(alert.get('event_id',''))[:8]}…`")
                    c3.markdown(
                        f"<span style='color:{color};font-weight:700'>Sev {sev}</span>",
                        unsafe_allow_html=True,
                    )
                    c4.markdown(f"📏 {alert.get('distance_km','—')} km")
                    if c5.button("✓ Ack", key=f"ack_{alert['id']}"):
                        r = post("/alerts/acknowledge",
                                 json={"alert_ids": [alert["id"]]})
                        if r:
                            st.success("Acknowledged")
                            st.rerun()
                    st.divider()

    with tab_all:
        all_alerts = get("/alerts", acknowledged=True, limit=50)
        if all_alerts:
            rows = [
                {
                    "Asset":      a.get("asset_name","—"),
                    "Type":       a.get("asset_type","—"),
                    "Distance km": a.get("distance_km","—"),
                    "Severity":   a.get("severity","—"),
                    "Acked":      a.get("acknowledged"),
                    "Time":       (a.get("created_at") or "")[:16].replace("T"," "),
                }
                for a in all_alerts
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No acknowledged alerts yet.")


# ══════════════════════════════════════════════════════════════════════════
#  PAGE 9 — Assets
# ══════════════════════════════════════════════════════════════════════════
elif page == PAGES[8]:
    st.title("🏢 Monitored Assets")

    assets = get("/assets") or []

    # ── Map ───────────────────────────────────────────────────────────────
    geo_assets = [a for a in assets if a.get("latitude") and a.get("longitude")]
    if geo_assets:
        asset_df = pd.DataFrame([
            {
                "lat":    a["latitude"],
                "lon":    a["longitude"],
                "name":   a["name"],
                "type":   a.get("asset_type", "—"),
                "radius": a.get("alert_radius_km", 5) * 1000,
            }
            for a in geo_assets
        ])
        fig_assets = go.Figure()
        # Draw alert-radius ring for each asset (real geodesic circle)
        for _, row in asset_df.iterrows():
            c_lats, c_lons = _circle_coords(row["lat"], row["lon"], row["radius"])
            fig_assets.add_trace(go.Scattermap(
                lat=c_lats, lon=c_lons,
                mode="lines",
                line=dict(width=2, color="rgba(52,152,219,0.80)"),
                fill="toself",
                fillcolor="rgba(52,152,219,0.08)",
                hoverinfo="skip",
                showlegend=False,
                name="",
            ))
        # Asset location markers on top
        fig_assets.add_trace(go.Scattermap(
            lat=asset_df["lat"],
            lon=asset_df["lon"],
            mode="markers+text",
            name="Assets",
            marker=dict(size=14, color="gold"),
            text=asset_df["name"],
            textposition="top right",
            hovertext=[
                f"<b>{r['name']}</b><br>Type: {r['type']}<br>"
                f"Alert radius: {r['radius']/1000:.0f} km"
                for _, r in asset_df.iterrows()
            ],
            hoverinfo="text",
        ))
        fig_assets.update_layout(**_osm_layout(lat=24.0, lon=78.0, zoom=4))
        st.plotly_chart(fig_assets, use_container_width=True)

    # ── Assets table ──────────────────────────────────────────────────────
    st.subheader(f"Assets ({len(assets)})")
    if assets:
        rows = [
            {
                "Name":       a["name"],
                "Type":       a.get("asset_type","—"),
                "Location":   a.get("location_name","—"),
                "Lat":        a.get("latitude","—"),
                "Lon":        a.get("longitude","—"),
                "Radius km":  a.get("alert_radius_km","—"),
                "Active":     a.get("active"),
            }
            for a in assets
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No assets. Add one below.")

    # ── Add asset form ────────────────────────────────────────────────────
    st.divider()
    with st.expander("➕ Add New Asset"):
        with st.form("add_asset"):
            name       = st.text_input("Name", placeholder="US Embassy New Delhi")
            asset_type = st.selectbox("Type", ["embassy", "government", "military",
                                                "infrastructure", "facility", "VIP", "other"])
            loc_name   = st.text_input("Location name", placeholder="Chanakyapuri, New Delhi")
            col1, col2 = st.columns(2)
            lat        = col1.number_input("Latitude",  value=28.5993, format="%.4f")
            lon        = col2.number_input("Longitude", value=77.1992, format="%.4f")
            radius     = st.slider("Alert radius (km)", 1.0, 50.0, 5.0, 0.5)
            submitted  = st.form_submit_button("Add Asset", type="primary")

        if submitted and name:
            result = post("/assets", json={
                "name":           name,
                "asset_type":     asset_type,
                "location_name":  loc_name,
                "latitude":       lat,
                "longitude":      lon,
                "alert_radius_km": radius,
            })
            if result and "id" in result:
                st.success(f"✅  Asset **{name}** added (id: `{result['id'][:8]}…`)")
                st.rerun()
            elif result:
                st.error(str(result))
