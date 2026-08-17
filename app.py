"""
Streamlit interface for the match simulator (modeling/simulate.py):
pick a home and away team, run the Monte Carlo simulation, and see
win/draw/loss odds, expected goals, and the full scoreline matrix.

Usage:
    streamlit run app.py
"""

import base64
import functools
import html
import os
import sys
import sqlite3

import plotly.graph_objects as go
import streamlit as st

from flags import flag_code_for_country

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "modeling"))
DB_PATH = os.path.join(BASE_DIR, "data", "football.db")
LOGOS_DIR = os.path.join(BASE_DIR, "static", "logos")
FLAGS_DIR = os.path.join(BASE_DIR, "static", "flags")

import simulate  # modeling/simulate.py

st.set_page_config(page_title="Match Simulator", page_icon="⚽", layout="centered")

# Sequential blue ramp (magnitude) and a blue/gray/red diverging triple for
# home win / draw / away win — see the dataviz skill's reference palette.
SEQUENTIAL_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
HOME_COLOR = "#2a78d6"
DRAW_COLOR = "#898781"
AWAY_COLOR = "#e34948"


@functools.lru_cache(maxsize=2048)
def _data_uri(path):
    """Base64-embeds a local image so the page needs no network at render
    time — download_media.py is what populates static/logos and static/flags."""
    try:
        with open(path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except FileNotFoundError:
        return None


def team_badge_html(name, country, team_id, flag_px=18, logo_px=26, stacked=False):
    code = flag_code_for_country(country)
    flag_uri = _data_uri(os.path.join(FLAGS_DIR, f"{code}.png")) if code else None
    logo_uri = _data_uri(os.path.join(LOGOS_DIR, f"{team_id}.png")) if team_id else None

    flag_img = (
        f'<img src="{flag_uri}" width="{flag_px}" '
        f'style="border-radius:2px;vertical-align:middle;margin-right:6px;">'
        if flag_uri else ""
    )
    logo_img = f'<img src="{logo_uri}" width="{logo_px}" style="object-fit:contain;">' if logo_uri else ""
    name_html = f'<span style="font-weight:600;vertical-align:middle;">{html.escape(name)}</span>'

    if stacked:
        return (
            f'<div style="text-align:center;">'
            f'<div>{flag_img}{name_html}</div>'
            f'<div style="margin-top:8px;">{logo_img}</div>'
            f'</div>'
        )
    return f"{flag_img}{logo_img}{name_html}"


@st.cache_resource
def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


@st.cache_data
def load_teams():
    conn = get_connection()
    return [row[0] for row in conn.execute("SELECT name FROM teams ORDER BY name")]


@st.cache_data
def get_team_media(name):
    conn = get_connection()
    row = conn.execute("SELECT country, id FROM teams WHERE name = ?", (name,)).fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def render_wdl_bar(result):
    fig = go.Figure()
    for label, prob, color in [
        (result["home_team"], result["prob_home_win"], HOME_COLOR),
        ("Draw", result["prob_draw"], DRAW_COLOR),
        (result["away_team"], result["prob_away_win"], AWAY_COLOR),
    ]:
        fig.add_trace(go.Bar(
            x=[prob * 100], y=["Outcome"], orientation="h", name=label,
            marker_color=color, text=f"{prob * 100:.1f}%",
            textposition="inside", insidetextanchor="middle",
            hovertemplate=f"{label}: {prob * 100:.1f}%<extra></extra>",
        ))
    fig.update_layout(
        barmode="stack", height=110,
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(visible=False, range=[0, 100]),
        yaxis=dict(visible=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_best_picks(result):
    st.subheader("🎯 Best picks")
    picks = result["best_picks"]
    if not picks:
        st.info(f"No selection cleared the {simulate.BEST_PICK_THRESHOLD * 100:.0f}% confidence bar for this match.")
        return
    for pick in picks:
        st.markdown(
            f"**{pick['selection']}** &nbsp;·&nbsp; _{pick['market']}_ "
            f"&nbsp;·&nbsp; **{pick['probability'] * 100:.1f}%**"
        )


def render_btts(result):
    btts = result["markets"]["btts"]
    c1, c2 = st.columns(2)
    c1.metric("BTTS — Yes", f"{btts['yes'] * 100:.1f}%")
    c2.metric("BTTS — No", f"{btts['no'] * 100:.1f}%")


def render_double_chance(result):
    dc = result["markets"]["double_chance"]
    home, away = result["home_team"], result["away_team"]
    c1, c2, c3 = st.columns(3)
    c1.metric(f"1X ({home} or Draw)", f"{dc['1X'] * 100:.1f}%")
    c2.metric(f"12 ({home} or {away})", f"{dc['12'] * 100:.1f}%")
    c3.metric(f"2X ({away} or Draw)", f"{dc['2X'] * 100:.1f}%")


def render_over_under_table(result):
    ou = result["markets"]["over_under"]
    rows = [
        {"Line": line, "Over %": round(p["over"] * 100, 1), "Under %": round(p["under"] * 100, 1)}
        for line, p in sorted(ou.items())
    ]
    st.dataframe(rows, hide_index=True, use_container_width=True)


def render_handicap_table(result):
    hc = result["markets"]["handicap"]
    home, away = result["home_team"], result["away_team"]
    rows = [
        {
            "Line (home)": f"{line:+g}",
            f"{home} covers %": round(p["home"] * 100, 1),
            f"{away} covers %": round(p["away"] * 100, 1),
            "Push %": round(p["push"] * 100, 1) if p["push"] > 0 else "—",
        }
        for line, p in sorted(hc.items())
    ]
    st.dataframe(rows, hide_index=True, use_container_width=True)


def render_scoreline_heatmap(result):
    max_g = simulate.MAX_DISPLAY_GOALS
    labels = [str(i) if i < max_g else f"{i}+" for i in range(max_g + 1)]
    matrix = [[0.0] * (max_g + 1) for _ in range(max_g + 1)]
    for scoreline, prob in result["scoreline_probs"].items():
        h, a = scoreline.replace("+", "").split("-")
        matrix[int(h)][int(a)] = prob * 100

    fig = go.Figure(data=go.Heatmap(
        z=matrix, x=labels, y=labels,
        colorscale=[[i / (len(SEQUENTIAL_BLUE) - 1), c] for i, c in enumerate(SEQUENTIAL_BLUE)],
        text=[[f"{v:.1f}" for v in row] for row in matrix],
        texttemplate="%{text}",
        textfont=dict(size=11),
        hovertemplate=(
            f"{result['home_team']} %{{y}} – %{{x}} {result['away_team']}"
            "<br>%{z:.1f}%<extra></extra>"
        ),
        colorbar=dict(title="Prob %"),
    ))
    fig.update_layout(
        xaxis_title=f"{result['away_team']} goals",
        yaxis_title=f"{result['home_team']} goals",
        margin=dict(l=10, r=10, t=10, b=10),
        height=440,
    )
    # Explicit category axes with one tick per cell — otherwise Plotly can
    # thin/misalign ticks against numeric-looking string labels like these,
    # and automargin leaves room for the titles instead of overlapping the plot.
    fig.update_xaxes(type="category", tickmode="array", tickvals=labels, ticktext=labels, automargin=True)
    fig.update_yaxes(type="category", tickmode="array", tickvals=labels, ticktext=labels,
                      autorange="reversed", automargin=True)
    st.plotly_chart(fig, use_container_width=True)


def main():
    st.title("⚽ Match Simulator")

    teams = load_teams()
    if not teams:
        st.error(f"No teams found in {DB_PATH}.")
        return

    col1, col2 = st.columns(2)
    with col1:
        home = st.selectbox("Home team", teams, index=0)
        home_country, home_id = get_team_media(home)
        st.markdown(team_badge_html(home, home_country, home_id), unsafe_allow_html=True)
    with col2:
        away_options = [t for t in teams if t != home]
        away = st.selectbox("Away team", away_options, index=min(1, len(away_options) - 1))
        away_country, away_id = get_team_media(away)
        st.markdown(team_badge_html(away, away_country, away_id), unsafe_allow_html=True)

    with st.expander("Options"):
        n_sims = st.slider("Number of simulations", 5000, 30000, 20000, step=5000)
        neutral = st.checkbox("Neutral venue (no home advantage)")
        save = st.checkbox("Save this prediction to the database", value=False)

    if st.button("Simulate match", type="primary", use_container_width=True):
        conn = get_connection()
        try:
            result = simulate.predict_match(
                conn, DB_PATH, home, away, n_sims=n_sims, save=save, neutral=neutral,
            )
        except ValueError as e:
            st.error(str(e))
            return

        st.divider()
        hc1, hvs, hc2 = st.columns([5, 1, 5])
        with hc1:
            st.markdown(
                team_badge_html(result["home_team"], home_country, home_id,
                                 flag_px=20, logo_px=72, stacked=True),
                unsafe_allow_html=True,
            )
        with hvs:
            st.markdown("<div style='text-align:center;color:#898781;font-weight:600;padding-top:36px;'>vs</div>",
                        unsafe_allow_html=True)
        with hc2:
            st.markdown(
                team_badge_html(result["away_team"], away_country, away_id,
                                 flag_px=20, logo_px=72, stacked=True),
                unsafe_allow_html=True,
            )
        st.caption(
            f"Elo {result['home_elo']:.0f} vs {result['away_elo']:.0f} · "
            f"based on {result['n_sims']:,} simulations"
        )

        render_wdl_bar(result)

        m1, m2, m3 = st.columns(3)
        m1.metric("Expected goals", f"{result['lambda_home']:.2f} – {result['lambda_away']:.2f}")
        m2.metric("Most likely score", result["most_likely_score"])
        m3.metric(
            "Outcome",
            max(
                [
                    (result["home_team"], result["prob_home_win"]),
                    ("Draw", result["prob_draw"]),
                    (result["away_team"], result["prob_away_win"]),
                ],
                key=lambda x: x[1],
            )[0],
        )

        st.divider()
        render_best_picks(result)

        st.divider()
        st.subheader("Markets")
        tab_btts, tab_dc, tab_ou, tab_hc = st.tabs(["BTTS", "Double chance", "Total goals", "Handicap"])
        with tab_btts:
            render_btts(result)
        with tab_dc:
            render_double_chance(result)
        with tab_ou:
            render_over_under_table(result)
        with tab_hc:
            render_handicap_table(result)

        st.divider()
        st.subheader("Scoreline probabilities")
        render_scoreline_heatmap(result)

        if result["prediction_id"]:
            st.success(f"Saved as prediction #{result['prediction_id']}")


if __name__ == "__main__":
    main()
