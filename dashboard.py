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

import json as _json
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


# ── NASA WorldWind globe helpers ──────────────────────────────────────────
# Self-contained HTML pages rendered in Streamlit via st.components.v1.html.
# WorldWind JS loaded from jsDelivr CDN (falls back to NASA CDN).
# Military/stealth HUD: corner brackets, classification banner, live coords,
# scanline texture, colour-coded threat legend with neon glow.

_WW_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
html, body { width:100%; height:HHpx; background:#000812; overflow:hidden;
             font-family:'Courier New',Courier,monospace; }
/* Stealth blue monochrome: desaturate → darken → sepia → hue-rotate into blue → boost.
   Earth terrain relief (mountains, coasts, deserts) stays visible as subtle blue shading.
   Event markers also turn blue (like sonar / radar pings). */
canvas#ww { width:100% !important; height:HHpx !important; display:block;
  filter: saturate(0) brightness(0.35) sepia(1) hue-rotate(185deg) saturate(4) brightness(0.82); }
.hud { position:fixed; top:0; left:0; right:0; bottom:0;
       pointer-events:none; z-index:20; }
.corner { position:absolute; width:28px; height:28px;
          border-color:rgba(0,170,255,0.55); border-style:solid; }
.tl { top:8px;    left:8px;   border-width:2px 0 0 2px; }
.tr { top:8px;    right:8px;  border-width:2px 2px 0 0; }
.bl { bottom:8px; left:8px;   border-width:0 0 2px 2px; }
.br { bottom:8px; right:8px;  border-width:0 2px 2px 0; }
.cls { position:absolute; top:10px; left:50%; transform:translateX(-50%);
       color:rgba(80,200,255,0.88); font-size:10px; font-weight:bold;
       letter-spacing:3px; white-space:nowrap;
       text-shadow:0 0 12px rgba(80,200,255,0.55); }
.sysinfo { position:absolute; top:30px; left:14px;
           color:rgba(60,180,240,0.58); font-size:8px; line-height:1.8;
           letter-spacing:1px; text-shadow:0 0 6px rgba(60,180,240,0.3); }
.coords  { position:absolute; bottom:14px; left:14px;
           color:rgba(80,200,255,0.78); font-size:9px; line-height:1.8;
           text-shadow:0 0 6px rgba(80,200,255,0.4); }
.legend  { position:absolute; top:34px; right:12px; min-width:178px;
           background:rgba(0,6,18,0.86); border:1px solid rgba(60,170,255,0.22);
           padding:9px 13px; font-size:9px; color:rgba(80,200,255,0.85);
           backdrop-filter:blur(3px); }
.ltitle  { font-weight:bold; letter-spacing:2px; font-size:8px;
           border-bottom:1px solid rgba(60,170,255,0.18);
           padding-bottom:4px; margin-bottom:5px; }
.li  { display:flex; align-items:center; gap:7px; margin:3px 0; }
.ld  { width:9px; height:9px; border-radius:50%; flex-shrink:0; }
.scan { position:absolute; top:0; left:0; right:0; bottom:0; pointer-events:none;
        background:repeating-linear-gradient(0deg,transparent,transparent 2px,
        rgba(0,0,0,0.042) 2px,rgba(0,0,0,0.042) 4px); }
#ldr { position:fixed; top:50%; left:50%; transform:translate(-50%,-50%);
       color:rgba(80,200,255,0.8); font-size:12px; letter-spacing:3px; z-index:100; }
"""

_WW_BOOT_JS = """
function loadWW(cb) {
  function tryLoad(url, fallback) {
    var s = document.createElement('script');
    s.src = url; s.onload = cb;
    s.onerror = fallback || function(){};
    document.head.appendChild(s);
  }
  tryLoad(
    'https://cdn.jsdelivr.net/npm/worldwindjs@1.7.0/build/dist/worldwind.min.js',
    function() {
      tryLoad('https://files.worldwind.arc.nasa.gov/artifactory/web/0.9.0/worldwind.min.js');
    }
  );
}

function baseGlobe(canvasId, cLat, cLon, rangeM) {
  WorldWind.Logger.setLoggingLevel(WorldWind.Logger.LEVEL_WARNING);
  var wwd = new WorldWind.WorldWindow(canvasId);
  wwd.navigator.lookAtLocation.latitude  = cLat;
  wwd.navigator.lookAtLocation.longitude = cLon;
  wwd.navigator.range = rangeM || 5.8e6;

  // 1. Starfield — black space behind globe
  var stars = new WorldWind.StarFieldLayer();
  stars.time = new Date();
  wwd.addLayer(stars);

  // 2. Atmospheric limb glow (blue haze on edge)
  var atmo = new WorldWind.AtmosphereLayer();
  atmo.time = new Date();
  wwd.addLayer(atmo);

  // 3. Blue Marble (single baked-in image — always loads, no tile server needed).
  //    The CSS filter on the canvas converts this into stealth monochrome blue
  //    while preserving terrain relief (mountains, coasts, deserts as blue shading).
  wwd.addLayer(new WorldWind.BMNGOneImageLayer());
  wwd.addLayer(new WorldWind.BMNGLandsatLayer());

  // 4. Country / coast borders in pale blue (matches monochrome theme)
  var borderLyr = new WorldWind.RenderableLayer('Borders');
  wwd.addLayer(borderLyr);
  var bAt = new WorldWind.ShapeAttributes(null);
  bAt.drawInterior = false;
  bAt.drawOutline  = true;
  bAt.outlineColor = new WorldWind.Color(0.55, 0.82, 1.0, 0.50);
  bAt.outlineWidth = 1.0;
  try {
    var gp = new WorldWind.GeoJSONParser(
      'https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/ne_110m_admin_0_countries.geojson'
    );
    gp.load(null, function(g, p) { return {attributes: bAt}; }, borderLyr);
  } catch(e) {}

  // 5. Compass + view controls
  wwd.addLayer(new WorldWind.CompassLayer());
  wwd.addLayer(new WorldWind.ViewControlsLayer(wwd));
  return wwd;
}

function hexWW(h, a) {
  return new WorldWind.Color(
    parseInt(h.slice(1,3),16)/255,
    parseInt(h.slice(3,5),16)/255,
    parseInt(h.slice(5,7),16)/255,
    a!==undefined ? a : 0.9);
}

function bindCoords(wwd, el) {
  wwd.addEventListener('mousemove', function(ev) {
    var pp = wwd.canvasCoordinates(ev.clientX, ev.clientY);
    var to = wwd.pickTerrain(pp).terrainObject();
    if (to && to.position) {
      var p = to.position, alt = Math.round(wwd.navigator.range / 1000);
      el.innerHTML =
        'LAT : ' + p.latitude.toFixed(4)  + '&deg; &nbsp;&nbsp;' +
        'LON : ' + p.longitude.toFixed(4) + '&deg;<br>' +
        'ALT : ' + alt + ' km &nbsp;&nbsp;' +
        'GRID: ' + (Math.floor(p.latitude/10)*10) + '/' + (Math.floor(p.longitude/10)*10);
    }
  });
}
"""


def _ww_page(title_banner: str, sysmode: str, body_js: str,
             hud_extra: str = "", height: int = 630) -> str:
    """Wrap WorldWind JS + HUD chrome into a self-contained HTML page."""
    css = _WW_CSS.replace("HH", str(height))
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>{css}</style>
</head><body>
<canvas id="ww"></canvas>
<div class="hud">
  <div class="scan"></div>
  <div class="corner tl"></div><div class="corner tr"></div>
  <div class="corner bl"></div><div class="corner br"></div>
  <div class="cls">&#9646; {title_banner} &#9646;</div>
  <div class="sysinfo">SYSTEM &nbsp;: ONLINE<br>MODE &nbsp;&nbsp;&nbsp;: {sysmode}<br>
    IMAGERY: BMNG / STEALTH<br>EPOCH &nbsp;&nbsp;: <span id="ep">--</span></div>
  <div class="coords" id="coords">LAT : --.----&deg; &nbsp; LON : --.----&deg;<br>
    ALT : ---- km &nbsp; GRID: --/--</div>
  {hud_extra}
  <div class="legend" id="leg"><div class="ltitle">&#9658; CLASSIFICATION</div></div>
</div>
<div id="ldr">&#9672; INITIALIZING WORLDWIND &#9672;</div>
<script>
(function(){{
document.getElementById('ep').textContent = new Date().toISOString().slice(0,19)+'Z';
{_WW_BOOT_JS}
{body_js}
loadWW(init);
}})();
</script>
</body></html>"""


def _worldwind_events_html(geo_events: list,
                            center_lat: float = 22.0,
                            center_lon: float = 82.0,
                            height: int = 630) -> str:
    """NASA WorldWind globe — events mode — military/stealth tactical UI."""
    ev_js  = _json.dumps([
        {"lat": float(e["latitude"]), "lon": float(e["longitude"]),
         "type": e.get("event_type", "UNKNOWN"),
         "title": (e.get("title") or "")[:80],
         "sev": int(e.get("severity") or 1)}
        for e in geo_events
    ])
    col_js = _json.dumps(EVENT_COLOURS)

    body_js = f"""
var EVENTS={ev_js}, COLORS={col_js}, C_LAT={center_lat}, C_LON={center_lon};
function init(){{
  document.getElementById('ldr').style.display='none';
  var wwd = baseGlobe('ww', C_LAT, C_LON, 5.8e6);

  var evtLyr = new WorldWind.RenderableLayer('Events');
  wwd.addLayer(evtLyr);

  var RINGS = 3;        // concentric ripple rings per event
  var CYCLE = 2500;     // ms for one full expand-and-fade cycle
  var ripples = [];
  var byType = {{}};

  EVENTS.forEach(function(e){{
    var ch  = COLORS[e.type] || '#95a5a6';
    var loc = new WorldWind.Location(e.lat, e.lon);
    var maxR = 50000 + (e.sev - 1) * 18000;   // 50-122 km

    // ── centre dot (always visible, bright) ──
    var dAt = new WorldWind.ShapeAttributes(null);
    dAt.drawInterior = true;
    dAt.interiorColor = hexWW(ch, 0.92);
    dAt.drawOutline = true;
    dAt.outlineColor = new WorldWind.Color(1,1,1,0.70);
    dAt.outlineWidth = 1.2;
    evtLyr.addRenderable(
      new WorldWind.SurfaceCircle(loc, 9000, dAt));

    // ── ripple rings (animated) ──
    for (var i = 0; i < RINGS; i++) {{
      var rAt = new WorldWind.ShapeAttributes(null);
      rAt.drawInterior = false;
      rAt.drawOutline  = true;
      rAt.outlineColor = hexWW(ch, 0.88);
      rAt.outlineWidth = 2.0;
      var ring = new WorldWind.SurfaceCircle(loc, 1, rAt);
      evtLyr.addRenderable(ring);
      ripples.push({{ c: ring, maxR: maxR, phase: i / RINGS, hex: ch }});
    }}
    byType[e.type] = (byType[e.type] || 0) + 1;
  }});

  // ── animation loop: expand rings, fade alpha, reset ──
  setInterval(function(){{
    var t = (Date.now() % CYCLE) / CYCLE;            // 0 → 1
    for (var i = 0; i < ripples.length; i++) {{
      var r = ripples[i];
      var p = (t + r.phase) % 1.0;                  // staggered phase
      r.c.radius = r.maxR * p;                      // expand outward
      var a = 0.88 * (1.0 - p);                     // fade as it grows
      r.c.attributes.outlineColor = hexWW(r.hex, a);
      r.c.attributes.outlineWidth = 2.2 - 1.2 * p;  // thinner at edge
    }}
    wwd.redraw();
  }}, 50);   // ~20 fps

  // ── legend ──
  var legEl = document.getElementById('leg');
  var lh = '<div class="ltitle">&#9658; THREAT CLASSIFICATION</div>';
  Object.keys(COLORS).forEach(function(t){{
    if (!byType[t]) return;
    lh += '<div class="li"><div class="ld" style="background:'+COLORS[t]
        + ';box-shadow:0 0 5px '+COLORS[t]+'88"></div><span>'+t
        + ' <span style="opacity:.55">('+byType[t]+')</span></span></div>';
  }});
  legEl.innerHTML = lh;

  bindCoords(wwd, document.getElementById('coords'));
  wwd.redraw();
}}
"""
    return _ww_page(
        title_banner="LUMIRA INTELLIGENCE SYSTEM — GLOBAL THREAT MONITOR",
        sysmode="THREAT VISUALIZATION",
        body_js=body_js,
        height=height,
    )


def _worldwind_assets_html(geo_assets: list,
                            center_lat: float = 24.0,
                            center_lon: float = 78.0,
                            height: int = 630) -> str:
    """NASA WorldWind globe — assets mode — military/stealth tactical UI."""
    as_js = _json.dumps([
        {"lat": float(a["latitude"]), "lon": float(a["longitude"]),
         "name": (a.get("name") or "Asset"),
         "type": (a.get("asset_type") or "—"),
         "radius": float(a.get("alert_radius_km", 5)) * 1000}
        for a in geo_assets
    ])

    body_js = f"""
var ASSETS={as_js}, C_LAT={center_lat}, C_LON={center_lon};
function init(){{
  document.getElementById('ldr').style.display='none';
  var wwd = baseGlobe('ww', C_LAT, C_LON, 5.8e6);
  var lyr = new WorldWind.RenderableLayer('Assets');
  wwd.addLayer(lyr);
  var RINGS = 2, CYCLE = 3000, ripples = [];
  var byType = {{}};

  ASSETS.forEach(function(a){{
    var loc = new WorldWind.Location(a.lat, a.lon);

    // ── alert-radius perimeter ring (static) ──
    var rAt = new WorldWind.ShapeAttributes(null);
    rAt.drawInterior = true;
    rAt.interiorColor = new WorldWind.Color(0.20, 0.60, 0.86, 0.06);
    rAt.drawOutline   = true;
    rAt.outlineColor  = new WorldWind.Color(0.20, 0.60, 0.86, 0.75);
    rAt.outlineWidth  = 2;
    lyr.addRenderable(new WorldWind.SurfaceCircle(loc, a.radius, rAt));

    // ── asset centre dot (bright) ──
    var dAt = new WorldWind.ShapeAttributes(null);
    dAt.drawInterior = true;
    dAt.interiorColor = new WorldWind.Color(1, 0.84, 0, 0.92);
    dAt.drawOutline   = true;
    dAt.outlineColor  = new WorldWind.Color(1, 1, 1, 1);
    dAt.outlineWidth  = 2;
    lyr.addRenderable(new WorldWind.SurfaceCircle(loc, 12000, dAt));

    // ── slow radar sweep inside the radius ──
    for (var i = 0; i < RINGS; i++) {{
      var sAt = new WorldWind.ShapeAttributes(null);
      sAt.drawInterior = false;
      sAt.drawOutline  = true;
      sAt.outlineColor = new WorldWind.Color(0.20, 0.60, 0.86, 0.70);
      sAt.outlineWidth = 1.8;
      var ring = new WorldWind.SurfaceCircle(loc, 1, sAt);
      lyr.addRenderable(ring);
      ripples.push({{ c: ring, maxR: a.radius, phase: i / RINGS }});
    }}
    byType[a.type] = (byType[a.type] || 0) + 1;
  }});

  // ── animation loop ──
  setInterval(function(){{
    var t = (Date.now() % CYCLE) / CYCLE;
    for (var i = 0; i < ripples.length; i++) {{
      var r = ripples[i];
      var p = (t + r.phase) % 1.0;
      r.c.radius = r.maxR * p;
      var a = 0.70 * (1.0 - p);
      r.c.attributes.outlineColor = new WorldWind.Color(0.20, 0.60, 0.86, a);
      r.c.attributes.outlineWidth = 1.8 - 0.8 * p;
    }}
    wwd.redraw();
  }}, 50);

  // ── legend ──
  var legEl = document.getElementById('leg');
  var lh = '<div class="ltitle">&#9658; ASSET CLASSIFICATION</div>';
  Object.keys(byType).forEach(function(t){{
    lh += '<div class="li"><div class="ld" style="background:#ffd700;box-shadow:0 0 5px #ffd70088"></div>'
        + '<span>'+t+' <span style="opacity:.55">('+byType[t]+')</span></span></div>';
  }});
  lh += '<div style="margin-top:6px;border-top:1px solid rgba(60,170,255,0.18);padding-top:5px;">'
      + '<div class="li"><div class="ld" style="background:rgba(52,152,219,0.8);box-shadow:0 0 5px #3498db88"></div>'
      + '<span>Alert perimeter + sweep</span></div></div>';
  legEl.innerHTML = lh;

  bindCoords(wwd, document.getElementById('coords'));
  wwd.redraw();
}}
"""
    return _ww_page(
        title_banner="LUMIRA INTELLIGENCE SYSTEM — ASSET PROTECTION MONITOR",
        sysmode="ASSET SURVEILLANCE",
        body_js=body_js,
        height=height,
    )


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
        st.components.v1.html(
            _worldwind_events_html(geo_events, center_lat=22.0, center_lon=82.0),
            height=650,
        )

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
        st.components.v1.html(
            _worldwind_assets_html(geo_assets, center_lat=24.0, center_lon=78.0),
            height=650,
        )

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
