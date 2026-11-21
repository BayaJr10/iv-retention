"""Look-and-feel helpers for the Streamlit app (brand colours, header, KPI cards).

Drop the business logo into  assets/logo.png  (or logo.svg / logo.jpg) and it is
used automatically in the header, sidebar and password screen. Without a file a
text wordmark is shown instead.
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parents[1] / "assets"

# Palette: deep plum (brand), warm ochre accent (African-print warmth), ivory surface
PLUM, PLUM_DARK, OCHRE = "#5B2A86", "#3E1B5E", "#C9831A"
INK, MUTED, SURFACE, CARD, LINE = "#241A2E", "#6B6275", "#FBF7F2", "#FFFFFF", "#EAE2D8"
GOOD, WARN = "#2E7D32", "#B26A00"   # status colours, always shown with an icon + words


def logo_path() -> Path | None:
    for name in ("logo.png", "logo.svg", "logo.jpg", "logo.jpeg", "logo.webp"):
        f = ASSETS / name
        if f.exists():
            return f
    return None


def icon_path() -> Path | None:
    f = ASSETS / "icon.png"
    return f if f.exists() else logo_path()


def _logo_data_uri() -> str | None:
    f = logo_path()
    if not f:
        return None
    mime = {"svg": "image/svg+xml", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(f.suffix[1:], f"image/{f.suffix[1:]}")
    return f"data:{mime};base64,{base64.b64encode(f.read_bytes()).decode()}"


def inject_css() -> None:
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=DM+Sans:wght@400;500;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, input, textarea, button {{ font-family: 'DM Sans', sans-serif; }}
h1, h2, h3, .iv-display {{ font-family: 'Fraunces', Georgia, serif !important; letter-spacing: -0.01em; color: {INK}; }}
.block-container {{ padding-top: 3.2rem; max-width: 1280px; }}
.iv-card .val.small {{ font-size:1.6rem; }}
[data-testid="stSidebar"] {{ background: {PLUM_DARK}; }}
[data-testid="stSidebar"] * {{ color: #F4EEF8 !important; }}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {{ background: rgba(255,255,255,.06); border: 1px dashed rgba(255,255,255,.35); }}
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] * {{ color: #F4EEF8 !important; }}
[data-testid="stSidebar"] button {{ background: rgba(255,255,255,.12) !important; border: 1px solid rgba(255,255,255,.3) !important; }}
[data-testid="stSidebar"] .iv-wordmark {{ color: {PLUM} !important; }}
[data-testid="stSidebar"] .iv-wordmark span {{ color: {OCHRE} !important; }}
.iv-side-step {{ font-size: .72rem; letter-spacing: .12em; text-transform: uppercase; opacity: .75; margin: 1.1rem 0 .2rem; }}

/* header */
.iv-header {{ display:flex; align-items:center; gap:18px; padding: 18px 22px; border-radius: 16px;
  background: linear-gradient(120deg, {PLUM} 0%, {PLUM_DARK} 70%); color: white; position: relative; overflow: hidden; }}
.iv-header::after {{ content:""; position:absolute; right:-40px; top:0; bottom:0; width:260px; opacity:.18;
  background: repeating-linear-gradient(45deg, {OCHRE} 0 10px, transparent 10px 22px),
              repeating-linear-gradient(-45deg, #fff 0 4px, transparent 4px 22px); }}
.iv-header img {{ height: 64px; width:auto; background:#141012; border-radius: 12px; padding: 6px 10px; }}
.iv-header .iv-title {{ font-family:'Fraunces',serif; font-size: 1.7rem; line-height:1.1; margin:0; color:#fff; }}
.iv-header .iv-sub {{ opacity:.85; font-size:.92rem; margin-top:4px; }}
.iv-wordmark {{ display:inline-block; font-family:'Fraunces',serif; font-weight:700; font-size:1.05rem; background:#fff; color:{PLUM};
  border-radius: 12px; padding: 10px 12px; line-height:1; text-align:center; }}
.iv-wordmark span {{ display:block; font-size:.62rem; letter-spacing:.22em; color:{OCHRE}; margin-top:3px; }}

/* chips */
.iv-chips {{ display:flex; flex-wrap:wrap; gap:8px; margin: 12px 0 4px; }}
.iv-chip {{ background:{CARD}; border:1px solid {LINE}; border-radius:999px; padding:4px 12px; font-size:.82rem; color:{MUTED}; }}
.iv-chip b {{ color:{INK}; }}
.iv-chip.warn {{ border-color:#F0D3A6; background:#FFF6E8; color:{WARN}; }}

/* KPI cards */
.iv-card {{ background:{CARD}; border:1px solid {LINE}; border-radius:14px; padding:16px 18px; min-height:150px;
  box-shadow: 0 1px 2px rgba(36,26,46,.04); }}
.iv-card .lbl {{ font-size:.8rem; color:{MUTED}; font-weight:500; min-height: 2.2em; }}
.iv-card .val {{ font-family:'Fraunces',serif; font-size:2.1rem; font-weight:700; color:{INK}; line-height:1.1; margin:4px 0 2px; }}
.iv-card .sub {{ font-size:.8rem; color:{MUTED}; }}
.iv-card.primary {{ border: 2px solid {PLUM}; }}
.iv-badge {{ display:inline-flex; align-items:center; gap:5px; font-size:.75rem; font-weight:700; border-radius:999px; padding:2px 9px; margin-top:8px; }}
.iv-badge.good {{ color:{GOOD}; background:#E8F4EA; }}
.iv-badge.warn {{ color:{WARN}; background:#FFF1DC; }}
.iv-badge.info {{ color:{PLUM}; background:#F1EAF7; }}
.iv-bar {{ height:6px; border-radius:6px; background:#EFE8F4; margin-top:10px; position:relative; overflow:hidden; }}
.iv-bar > i {{ position:absolute; left:0; top:0; bottom:0; border-radius:6px; background:{PLUM}; }}
.iv-bar > em {{ position:absolute; top:-3px; bottom:-3px; width:2px; background:{INK}; }}

/* tabs */
.stTabs [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid {LINE}; }}
.stTabs [data-baseweb="tab"] {{ padding: 10px 16px; border-radius: 10px 10px 0 0; font-weight:500; }}
.stTabs [aria-selected="true"] {{ background: {CARD}; color:{PLUM} !important; }}
[data-testid="stMetricValue"] {{ font-size: 1.55rem !important; }}
[data-testid="stMetric"] {{ background:{CARD}; border:1px solid {LINE}; border-radius:14px; padding:14px 16px; }}
.stDownloadButton button, .stButton button {{ border-radius:10px; font-weight:600; }}
.iv-howto {{ background:#F6F0FA; border-left:4px solid {PLUM}; border-radius:10px; padding:12px 16px; color:{INK}; font-size:.92rem; }}
@media (max-width: 640px) {{ .iv-header .iv-title {{ font-size:1.25rem; }} .iv-header::after {{ display:none; }} }}
</style>""", unsafe_allow_html=True)


def logo_html(height: int = 56) -> str:
    uri = _logo_data_uri()
    if uri:
        return (f'<img src="{uri}" alt="Immaculate Vibe 254 logo" style="height:{height}px;width:auto;'
                f'background:#141012;border-radius:12px;padding:8px 12px">')
    return '<div class="iv-wordmark">Immaculate<br>Vibes<span>NAIROBI</span></div>'


def sidebar_logo() -> None:
    f = logo_path()
    if f and hasattr(st, "logo"):
        st.logo(str(f), size="large")
    else:
        st.sidebar.markdown(f'<div style="margin-bottom:6px">{logo_html()}</div>', unsafe_allow_html=True)


def header(title: str, subtitle: str) -> None:
    st.markdown(f"""<div class="iv-header">{logo_html()}<div>
<p class="iv-title">{html.escape(title)}</p><div class="iv-sub">{subtitle}</div></div></div>""",
                unsafe_allow_html=True)


def chips(items: list[tuple[str, str]], warn: set[int] | None = None) -> None:
    warn = warn or set()
    body = "".join(f'<span class="iv-chip{" warn" if i in warn else ""}">{html.escape(a)} <b>{html.escape(b)}</b></span>'
                   for i, (a, b) in enumerate(items))
    st.markdown(f'<div class="iv-chips">{body}</div>', unsafe_allow_html=True)


def kpi_card(col, label: str, value: str, sub: str = "", status: str | None = None,
             progress: float | None = None, target_mark: float | None = None, primary: bool = False) -> None:
    """status: 'good' | 'warn' | 'info' (shown as icon + words, never colour alone)."""
    badge = ""
    if status == "good":
        badge = '<span class="iv-badge good">✓ On track</span>'
    elif status == "warn":
        badge = '<span class="iv-badge warn">▲ Below target</span>'
    elif status == "info":
        badge = '<span class="iv-badge info">ⓘ Watch</span>'
    bar = ""
    if progress is not None:
        p = max(0.0, min(progress, 1.0)) * 100
        mark = f'<em style="left:{max(0, min(target_mark, 1)) * 100:.1f}%"></em>' if target_mark is not None else ""
        bar = f'<div class="iv-bar"><i style="width:{p:.1f}%"></i>{mark}</div>'
    col.markdown(f"""<div class="iv-card{' primary' if primary else ''}"><div class="lbl">{html.escape(label)}</div>
<div class="val{' small' if len(value) > 9 else ''}">{html.escape(value)}</div><div class="sub">{sub}</div>{bar}{badge}</div>""", unsafe_allow_html=True)


def howto(text_md: str) -> None:
    st.markdown(f'<div class="iv-howto">{text_md}</div>', unsafe_allow_html=True)


def side_step(label: str) -> None:
    st.sidebar.markdown(f'<div class="iv-side-step">{html.escape(label)}</div>', unsafe_allow_html=True)
