"""When Rivers Speak — a national river observatory.

A marimo dashboard over public USGS river data. Run it two ways:

    marimo run app.py        # served, read-only app (what Hugging Face uses)
    marimo edit app.py       # notebook editor

Data comes from a local DuckDB built by ``scripts/build_database.py`` from
partitioned Parquet. In demo mode the small bundled sample under ``data/sample``
is used; set ``RIVERS_DATA_MODE=full`` and ``RIVERS_DATA_DIR`` to point at a
larger locally-fetched store.

This is an exploratory, situational-awareness tool for making river change
visible. It is not a flood-prediction system and carries no emergency
reliability guarantees.
"""
import marimo

__generated_with = "0.9"
app = marimo.App(width="full", app_title="When Rivers Speak")


@app.cell
def _imports():
    import os
    from pathlib import Path

    import altair as alt
    import marimo as mo
    import pandas as pd

    from rivers import anomalies, build_duckdb, charts, config, maps, metrics
    return (Path, alt, anomalies, build_duckdb, charts, config,
            maps, metrics, mo, os, pd)


@app.cell
def _database(build_duckdb, config, mo):
    # Open the analytical database (read-only). If it is missing, build it from
    # whatever Parquet is present so the app is self-healing on first run.
    if not config.DUCKDB_PATH.exists():
        if config.PARQUET_DIR.exists():
            build_duckdb.build()
        else:
            raise FileNotFoundError(
                f"No database or Parquet found under {config.DATA_DIR}. "
                "Run scripts/fetch_demo_data.py then scripts/build_database.py."
            )
    con = build_duckdb.connect(read_only=True)

    # Which parameters actually have data (so the UI only offers real choices).
    _params_present = con.execute(
        "SELECT DISTINCT parameter FROM daily_values"
    ).df()["parameter"].tolist()
    param_options = {
        p.label: p.key for p in config.PARAMETERS if p.key in _params_present
    } or {config.PARAMETERS[0].label: config.PARAMETERS[0].key}

    _states_present = sorted(
        con.execute("SELECT DISTINCT state FROM sites").df()["state"].tolist()
    )
    state_options = {config.state_name(s): s for s in _states_present}
    mode_badge = "demo" if config.is_demo() else "full"
    return con, mode_badge, param_options, state_options


@app.cell
def _header(mo, mode_badge, con):
    n_sites = con.execute("SELECT count(*) FROM sites").fetchone()[0]
    n_states = con.execute("SELECT count(DISTINCT state) FROM sites").fetchone()[0]
    n_obs = con.execute("SELECT count(*) FROM daily_values").fetchone()[0]
    mo.md(
        f"""
        # 🌊 When Rivers Speak
        ### A national river observatory

        Public **USGS** water data turned into an interactive tool for exploring
        how rivers change across space and time — historical trends, latest
        conditions, anomaly detection, and high-performance maps.

        <span style="color:#2c7fb8">**{n_sites:,}** monitoring sites</span> ·
        **{n_states}** states · **{n_obs:,}** daily observations ·
        mode: `{mode_badge}`

        > This is a situational-awareness and exploratory data tool. It is **not**
        > a flood-prediction system and carries no emergency reliability guarantees.
        """
    )
    return


@app.cell
def _controls(mo, param_options, state_options):
    # Global, shared widgets used across tabs.
    all_states = list(state_options.values())
    state_select = mo.ui.multiselect(
        options=state_options, value=list(state_options.keys()),
        label="States",
    )
    param_select = mo.ui.dropdown(
        options=param_options, value=list(param_options.keys())[0],
        label="Parameter",
    )
    anomaly_threshold = mo.ui.slider(
        start=0, stop=100, step=5, value=0, label="Min anomaly score",
        show_value=True,
    )
    anomalous_only = mo.ui.checkbox(value=False, label="Show only elevated (score ≥ 50)")
    layer_select = mo.ui.dropdown(
        options={"Site points": "points", "H3 hex aggregation": "h3"},
        value="Site points", label="Map layer",
    )
    controls = mo.hstack(
        [state_select, param_select, layer_select, anomaly_threshold, anomalous_only],
        justify="start", gap=1.5, wrap=True,
    )
    return (all_states, anomalous_only, anomaly_threshold, controls,
            layer_select, param_select, state_select)


@app.cell
def _tabs(mo):
    mo.md("## Explore")
    return


# --------------------------------------------------------------------------- #
# Shared data pulls (small, filtered DuckDB queries — never full national loads)
# --------------------------------------------------------------------------- #
@app.cell
def _selection(anomalous_only, anomaly_threshold, param_select, state_select):
    sel_states = list(state_select.value) if state_select.value else None
    sel_param = param_select.value
    min_score = 50 if anomalous_only.value else (anomaly_threshold.value or None)
    return min_score, sel_param, sel_states


@app.cell
def _pulse_data(build_duckdb, con, min_score, sel_param, sel_states):
    pulse = build_duckdb.latest_map_frame(
        con, parameter=sel_param, states=sel_states, min_anomaly=min_score
    )
    return (pulse,)


# =========================================================================== #
# TAB 1 — National River Pulse
# =========================================================================== #
@app.cell
def _tab_pulse(charts, controls, layer_select, maps, mo, pulse, sel_param):
    mo.md("### 1 · National River Pulse")

    # Summary cards.
    n = len(pulse)
    n_extreme = int((pulse["anomaly_score"] >= 70).sum()) if n else 0
    n_high = int((pulse["anomaly_score"] >= 50).sum()) if n else 0
    med_pctl = pulse["percentile"].median() if n else float("nan")
    cards = mo.hstack([
        mo.stat(f"{n:,}", label="Sites shown", bordered=True),
        mo.stat(f"{n_high:,}", label="Elevated (≥50)", bordered=True),
        mo.stat(f"{n_extreme:,}", label="Extreme (≥70)", bordered=True),
        mo.stat(f"{med_pctl:.0f}" if n else "—", label="Median percentile", bordered=True),
    ], justify="start", gap=1)

    # Map (pydeck if available, else a scatter fallback). The layer style
    # follows the shared "Map layer" selector; H3 falls back to points if the
    # h3 library is unavailable.
    _layer = layer_select.value or "points"
    deck = maps.build_deck(pulse, layer=_layer) if maps.mapping_available() else None
    if deck is not None and len(pulse):
        map_view = mo.iframe(deck.to_html(as_string=True, notebook_display=False),
                             height="560px")
    elif deck is not None:
        map_view = mo.md("_No sites match the current filters._")
    else:
        map_view = mo.md("_Mapping stack unavailable; showing the table below._")

    # Top anomalous sites table.
    cols = ["site_no", "station_nm", "state", "latest_value", "percentile",
            "anomaly_score", "anomaly_level", "latest_date"]
    top = (pulse.dropna(subset=["anomaly_score"])
                .sort_values("anomaly_score", ascending=False)
                .head(25)[cols])
    top_table = mo.ui.table(top, selection=None, pagination=True, page_size=10)

    mo.vstack([
        controls,
        cards,
        mo.md("#### Latest river conditions"),
        map_view,
        mo.md("#### Most anomalous sites"),
        top_table,
    ])
    return


# =========================================================================== #
# TAB 2 — Historical Explorer
# =========================================================================== #
@app.cell
def _hist_site_picker(build_duckdb, con, mo, sel_param, sel_states):
    site_df = build_duckdb.site_list(con, parameter=sel_param, states=sel_states)
    site_opts = {
        f"{r.station_nm or r.site_no} ({r.state})": r.site_no
        for r in site_df.itertuples()
    } or {"(no sites)": None}
    site_search = mo.ui.dropdown(
        options=site_opts, value=list(site_opts.keys())[0],
        label="Site", searchable=True,
    )
    date_range = mo.ui.date_range(
        start="2000-01-01",
        label="Date range (leave wide for full record)",
    )
    show_roll = mo.ui.checkbox(value=True, label="Rolling 7/30-day means")
    show_band = mo.ui.checkbox(value=True, label="Seasonal normal band")
    return date_range, show_band, show_roll, site_search


@app.cell
def _tab_hist(alt, build_duckdb, charts, con, config, date_range, metrics,
              mo, pd, sel_param, show_band, show_roll, site_search):
    mo.md("### 2 · Historical Explorer")
    hist_site_no = site_search.value
    hist_ctl = mo.hstack([site_search, date_range, show_roll, show_band],
                         justify="start", gap=1.2, wrap=True)

    if not hist_site_no:
        hist_out = mo.md("_No site selected._")
    else:
        hist_d0 = date_range.value[0] if date_range.value else None
        hist_d1 = date_range.value[1] if date_range.value else None
        hist_ser = build_duckdb.site_series(con, site_no=hist_site_no,
                                            parameter=sel_param,
                                            start=hist_d0, end=hist_d1)
        if hist_ser.empty:
            hist_out = mo.md("_No data for this site in the selected range._")
        else:
            hist_plot = (metrics.rolling_means(hist_ser, windows=(7, 30))
                         if show_roll.value else hist_ser.copy())
            hist_plot["date"] = pd.to_datetime(hist_plot["date"]).astype(str)

            hist_clim_df = None
            if show_band.value:
                hist_clim = build_duckdb.site_climatology_frame(
                    con, site_no=hist_site_no, parameter=sel_param)
                if not hist_clim.empty:
                    hist_sd = pd.to_datetime(hist_ser["date"])
                    hist_tmp = pd.DataFrame({"date": hist_sd,
                                             "doy": hist_sd.dt.dayofyear})
                    hist_clim_df = hist_tmp.merge(hist_clim, on="doy", how="left")[
                        ["date", "p25", "p75"]]
                    hist_clim_df["date"] = hist_clim_df["date"].astype(str)

            hist_plabel = config.resolve_parameter(sel_param).label
            hist_unit = config.resolve_parameter(sel_param).unit
            hist_chart = charts.time_series(
                hist_plot, value_label=f"{hist_plabel} ({hist_unit})",
                title=f"{hist_site_no} — {hist_plabel}", climatology=hist_clim_df,
            )
            hist_cards = anomaly_summary(hist_ser, hist_site_no, sel_param)
            hist_out = mo.vstack([hist_cards, mo.ui.altair_chart(hist_chart)])

    mo.vstack([hist_ctl, hist_out])
    return


@app.cell
def _anomaly_summary_fn(anomalies, mo):
    def anomaly_summary(ser, site_no, parameter):
        r = anomalies.score_site(ser, site_no, parameter)
        return mo.hstack([
            mo.stat(f"{r.anomaly_score:.0f}", label="Anomaly score", bordered=True),
            mo.stat(f"{r.percentile:.0f}" if r.percentile == r.percentile else "—",
                    label="Percentile", bordered=True),
            mo.stat(f"{r.latest_value:,.1f}", label="Latest value", bordered=True),
            mo.stat(f"{r.completeness:.0%}", label="Completeness", bordered=True),
            mo.stat(anomalies.anomaly_level(r.anomaly_score), label="Level", bordered=True),
        ], justify="start", gap=1)
    return (anomaly_summary,)


# =========================================================================== #
# TAB 3 — State Comparison
# =========================================================================== #
@app.cell
def _tab_state(alt, charts, con, mo, sel_param):
    mo.md("### 3 · State Comparison")
    sa = con.execute(
        "SELECT * FROM state_anomaly_summary WHERE parameter = ? ORDER BY anomaly_burden DESC",
        [sel_param]).df()
    if sa.empty:
        state_out = mo.md("_No state summaries for this parameter._")
    else:
        rank_chart = charts.state_ranking(sa, metric="anomaly_burden",
                                          title="State anomaly burden")
        state_scores = con.execute(
            "SELECT state, anomaly_score FROM site_anomaly_scores WHERE parameter = ?",
            [sel_param]).df()
        dist_chart = charts.score_distribution(state_scores, by="state")
        state_table = mo.ui.table(
            sa[["state", "n_sites", "mean_anomaly", "max_anomaly",
                "n_elevated", "anomaly_burden"]].round(1),
            selection=None, pagination=True, page_size=12)
        state_out = mo.vstack([
            mo.hstack([mo.ui.altair_chart(rank_chart), mo.ui.altair_chart(dist_chart)],
                      widths=[1, 1], gap=1),
            mo.md("#### State ranking"),
            state_table,
        ])
    state_out
    return


# =========================================================================== #
# TAB 4 — River Change Detector
# =========================================================================== #
@app.cell
def _tab_change(alt, charts, con, mo, sel_param, sel_states):
    mo.md("### 4 · River Change Detector")
    chg_where = "parameter = ?"
    chg_params = [sel_param]
    if sel_states:
        chg_where += " AND state IN (" + ",".join("?" for _ in sel_states) + ")"
        chg_params += sel_states
    chg_sc = con.execute(
        f"SELECT * FROM site_anomaly_scores WHERE {chg_where}", chg_params).df()
    if chg_sc.empty:
        chg_out = mo.md("_No anomaly scores for this selection._")
    else:
        chg_rises = charts.change_ranking(chg_sc, direction="rise", title="Top sudden rises")
        chg_drops = charts.change_ranking(chg_sc, direction="drop", title="Top sudden drops")
        chg_vol = (chg_sc.dropna(subset=["volatility"])
                   .sort_values("volatility", ascending=False)
                   .head(20)[["site_no", "state", "volatility", "anomaly_score"]])
        chg_vol_chart = (alt.Chart(chg_vol).mark_bar(color="#41b6c4").encode(
            x=alt.X("volatility:Q", title="Volatility index"),
            y=alt.Y("site_no:N", sort="-x", title="Site"),
            tooltip=["site_no", "state", "volatility"],
        ).properties(title="Most volatile rivers (30-day CV)",
                     width="container", height=420))
        chg_out = mo.vstack([
            mo.hstack([mo.ui.altair_chart(chg_rises), mo.ui.altair_chart(chg_drops)],
                      widths=[1, 1], gap=1),
            mo.ui.altair_chart(chg_vol_chart),
        ])
    chg_out
    return


# =========================================================================== #
# TAB 5 — Data Coverage Observatory
# =========================================================================== #
@app.cell
def _tab_coverage(alt, charts, con, mo):
    mo.md("### 5 · Data Coverage Observatory")
    cov = con.execute("SELECT * FROM data_coverage_summary ORDER BY n_sites DESC").df()
    if cov.empty:
        cov_out = mo.md("_No coverage summary available._")
    else:
        cov_chart = charts.coverage_bars(cov)
        # Site longevity: mean days of record per state.
        cov_long_chart = (alt.Chart(cov).mark_bar(color="#253494").encode(
            x=alt.X("mean_days_per_site:Q", title="Mean days of record per site"),
            y=alt.Y("state:N", sort="-x", title="State"),
            color=alt.Color("parameter:N", title="Parameter"),
            tooltip=["state", "parameter", "mean_days_per_site", "mean_completeness"],
        ).properties(title="Site longevity by state", width="container", height=340))
        cov_show = cov[["state", "parameter", "n_sites", "total_obs",
                        "mean_completeness", "earliest_record", "latest_record"]].copy()
        cov_show["mean_completeness"] = cov_show["mean_completeness"].round(3)
        cov_table = mo.ui.table(cov_show, selection=None, pagination=True, page_size=12)
        cov_out = mo.vstack([
            mo.hstack([mo.ui.altair_chart(cov_chart), mo.ui.altair_chart(cov_long_chart)],
                      widths=[1, 1], gap=1),
            cov_table,
        ])
    cov_out
    return


# =========================================================================== #
# TAB 6 — Live 15-Minute Signal
# =========================================================================== #
@app.cell
def _iv_site_picker(build_duckdb, con, mo, sel_param, sel_states):
    iv_sites = build_duckdb.iv_site_list(con, parameter=sel_param, states=sel_states)
    iv_opts = {
        f"{r.station_nm or r.site_no} ({r.state}) — {int(r.n_readings):,} readings": r.site_no
        for r in iv_sites.itertuples()
    } or {"(no 15-minute data for this selection)": None}
    iv_site = mo.ui.dropdown(
        options=iv_opts, value=list(iv_opts.keys())[0],
        label="Site (15-minute data)", searchable=True,
    )
    iv_band = mo.ui.checkbox(value=True, label="Shade seasonal normal range (daily p25–p75)")
    return iv_band, iv_site, iv_sites


@app.cell
def _tab_iv(build_duckdb, charts, con, config, iv_band, iv_site, metrics,
            mo, pd, sel_param):
    mo.md("### 6 · Live 15-Minute Signal")
    iv_ctl = mo.hstack([iv_site, iv_band], justify="start", gap=1.2, wrap=True)
    iv_no = iv_site.value

    if not iv_no:
        iv_out = mo.md(
            "_No 15-minute data for this state/parameter selection. "
            "Instantaneous coverage is densest for streamflow and gage height._"
        )
    else:
        ivs = build_duckdb.site_iv_series(con, site_no=iv_no, parameter=sel_param)
        if ivs.empty:
            iv_out = mo.md("_No instantaneous readings stored for this site._")
        else:
            # Seasonal normal band from the site's daily p25/p75 climatology.
            nlow = nhigh = None
            if iv_band.value:
                clim = build_duckdb.site_climatology_frame(
                    con, site_no=iv_no, parameter=sel_param)
                if not clim.empty:
                    nlow = float(clim["p25"].median())
                    nhigh = float(clim["p75"].median())
            iv_plabel = config.resolve_parameter(sel_param).label
            iv_unit = config.resolve_parameter(sel_param).unit
            iv_n = len(ivs)
            iv_span_h = (ivs["ts"].max() - ivs["ts"].min()).total_seconds() / 3600.0
            iv_cadence = (iv_span_h * 60.0 / iv_n) if iv_n > 1 else float("nan")
            iv_cards = mo.hstack([
                mo.stat(f"{iv_n:,}", label="15-min readings", bordered=True),
                mo.stat(f"{ivs['value'].iloc[-1]:,.2f}", label=f"Latest {iv_plabel.lower()}", bordered=True),
                mo.stat(f"{ivs['value'].min():,.1f} – {ivs['value'].max():,.1f}",
                        label="Range (window)", bordered=True),
                mo.stat(f"~{iv_cadence:.0f} min" if iv_cadence == iv_cadence else "—",
                        label="Mean cadence", bordered=True),
            ], justify="start", gap=1)
            iv_chart = charts.instantaneous_series(
                ivs, value_label=f"{iv_plabel} ({iv_unit})",
                title=f"{iv_no} — {iv_plabel}, last ~30 days at 15-minute resolution",
                normal_low=nlow, normal_high=nhigh,
            )
            iv_out = mo.vstack([iv_cards, mo.ui.altair_chart(iv_chart)])

    mo.vstack([
        mo.md(
            "High-resolution instantaneous readings (typically every 15 minutes) "
            "for the last ~30 days, tracked against the gauge's seasonal normal "
            "range — a real-time-monitoring view of each parameter."
        ),
        iv_ctl,
        iv_out,
    ])
    return


# =========================================================================== #
# TAB 7 — About the Data
# =========================================================================== #
@app.cell
def _tab_about(mo, mode_badge):
    mo.md(
        f"""
        ### 7 · About the Data

        **Source.** All data comes from the U.S. Geological Survey (USGS) Water
        Services API (`waterservices.usgs.gov`) — the Site, Daily Values, and
        Instantaneous Values services. USGS data are in the public domain.

        **What this shows.** Historical daily statistics, the most recent
        observation per gauge, a trailing ~30-day window of **15-minute
        instantaneous readings**, and derived metrics (percentiles, day-of-year
        climatology, rolling means, volatility) combined into a composite
        *anomaly score* for exploratory situational awareness.

        **Parameters.** Streamflow / discharge, gage height, water temperature,
        specific conductance, dissolved oxygen, and pH. Streamflow and gage
        height have the densest coverage; water-quality parameters are reported
        at progressively fewer gauges.

        **Time resolution.** Daily means span 2021–present (multi-year trends and
        climatology). The Live 15-Minute Signal tab shows the last ~30 days of
        sub-hourly instantaneous data — the real-time-monitoring view. Six years
        of 15-minute data nationwide would be billions of rows, so only the
        recent high-resolution window is bundled.

        **Anomaly score.** A blend of five signals, clipped to 0–100:

        > `percentile_extremeness + rapid_change + seasonal_deviation
        >  + persistence − missing_data_penalty`

        It flags *unusual* conditions relative to a gauge's own record; it is
        **not** a calibrated hydrologic forecast.

        **Update frequency.** In demo mode the bundled sample is refreshed
        periodically. The latest-observations slice can be updated on a schedule
        (see `scripts/update_latest.py` and the GitHub Action).

        **Limitations.**

        - Provisional data may be revised by USGS after review.
        - Coverage varies widely by state, parameter, and era.
        - Gaps, sensor drift, and datum changes are not fully corrected.
        - Missing readings (USGS marks these with a `-999999` sentinel) and
          physically-impossible values (e.g. pH outside 0–14, water temperature
          above 45 °C) are dropped during ingest, so they do not distort
          statistics or anomaly scores. Real extremes — including strong
          negative discharge at tidal gauges (reverse flow) — are preserved.
        - The anomaly score is heuristic and exploratory.

        **This is not** a flood-prediction system, an emergency alerting service,
        or a substitute for official NWS/USGS advisories. Current mode:
        `{mode_badge}`.

        **Reproducibility.** Fetch → normalize to partitioned Parquet → build
        DuckDB summary tables → serve. Rebuild with `scripts/fetch_demo_data.py`
        and `scripts/build_database.py`. See `docs/architecture.md`.
        """
    )
    return


if __name__ == "__main__":
    app.run()
