"""
International Cost of Capital App
---------------------------------
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Method (Rebelo, International Valuation, 2026):
  D/E          = d / (1 - d)
  beta_E       = beta_U * [1 + (1 - tau) * D/E]                  (relever, zero debt beta)
  Re  (USD)    = Rf_US + beta_E * ERP                            (CAPM, no country premium)
  F            = (1 + Rf*) / (1 + Rf_US)                         (Fisher + relative PPP)
  Re* (local)  = (1 + Re) * F - 1
  Rd  (USD)    = Rf_US + s                                       (s = USD BBB spread)
  s*  (local)  = s * F    (default; user may override)
  Rd* (local)  = Rf* + s*
  WACC         = (1 - d) * Re + d * Rd * (1 - tau)               (same d, tau, ERP in both currencies)
"""

from __future__ import annotations

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
OECD_SDMX_URLS = [
    # Corporate Tax Statistics – statutory CIT rates (all Inclusive Framework jurisdictions)
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_CTS_CIT@DF_CTS_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels",
    # OECD Tax Database – statutory CIT rates (OECD members)
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions&format=csvfilewithlabels",
]
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5   # %
DEFAULT_DEBT = 30.0  # %

# country -> (ISO3, currency, Trading Economics slug, aliases used on TE tables)
COUNTRIES: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "United States": ("USA", "USD", "united-states", ("US", "U.S.", "USA")),
    "Argentina": ("ARG", "ARS", "argentina", ()),
    "Australia": ("AUS", "AUD", "australia", ()),
    "Austria": ("AUT", "EUR", "austria", ()),
    "Belgium": ("BEL", "EUR", "belgium", ()),
    "Brazil": ("BRA", "BRL", "brazil", ()),
    "Canada": ("CAN", "CAD", "canada", ()),
    "Chile": ("CHL", "CLP", "chile", ()),
    "China": ("CHN", "CNY", "china", ()),
    "Colombia": ("COL", "COP", "colombia", ()),
    "Czech Republic": ("CZE", "CZK", "czech-republic", ("Czechia",)),
    "Denmark": ("DNK", "DKK", "denmark", ()),
    "Egypt": ("EGY", "EGP", "egypt", ()),
    "Finland": ("FIN", "EUR", "finland", ()),
    "France": ("FRA", "EUR", "france", ()),
    "Germany": ("DEU", "EUR", "germany", ()),
    "Greece": ("GRC", "EUR", "greece", ()),
    "Hungary": ("HUN", "HUF", "hungary", ()),
    "India": ("IND", "INR", "india", ()),
    "Indonesia": ("IDN", "IDR", "indonesia", ()),
    "Ireland": ("IRL", "EUR", "ireland", ()),
    "Israel": ("ISR", "ILS", "israel", ()),
    "Italy": ("ITA", "EUR", "italy", ()),
    "Japan": ("JPN", "JPY", "japan", ()),
    "Kenya": ("KEN", "KES", "kenya", ()),
    "Malaysia": ("MYS", "MYR", "malaysia", ()),
    "Mexico": ("MEX", "MXN", "mexico", ()),
    "Netherlands": ("NLD", "EUR", "netherlands", ()),
    "New Zealand": ("NZL", "NZD", "new-zealand", ()),
    "Nigeria": ("NGA", "NGN", "nigeria", ()),
    "Norway": ("NOR", "NOK", "norway", ()),
    "Peru": ("PER", "PEN", "peru", ()),
    "Philippines": ("PHL", "PHP", "philippines", ()),
    "Poland": ("POL", "PLN", "poland", ()),
    "Portugal": ("PRT", "EUR", "portugal", ()),
    "Romania": ("ROU", "RON", "romania", ()),
    "Singapore": ("SGP", "SGD", "singapore", ()),
    "Slovakia": ("SVK", "EUR", "slovakia", ()),
    "South Africa": ("ZAF", "ZAR", "south-africa", ()),
    "South Korea": ("KOR", "KRW", "south-korea", ("Korea",)),
    "Spain": ("ESP", "EUR", "spain", ()),
    "Sweden": ("SWE", "SEK", "sweden", ()),
    "Switzerland": ("CHE", "CHF", "switzerland", ()),
    "Thailand": ("THA", "THB", "thailand", ()),
    "Turkey": ("TUR", "TRY", "turkey", ("Türkiye", "Turkiye")),
    "United Kingdom": ("GBR", "GBP", "united-kingdom", ("UK", "U.K.", "Britain", "Great Britain")),
    "Vietnam": ("VNM", "VND", "vietnam", ("Viet Nam",)),
}

# --- Bundled fallbacks (used ONLY if the live fetch fails; always labelled in the UI) ---
# Combined statutory CIT rates (%), OECD Corporate Tax Statistics snapshot.
FALLBACK_TAX = {
    "USA": 25.6, "ARG": 35.0, "AUS": 30.0, "AUT": 23.0, "BEL": 25.0, "BRA": 34.0, "CAN": 26.1,
    "CHL": 27.0, "CHN": 25.0, "COL": 35.0, "CZE": 21.0, "DNK": 22.0, "EGY": 22.5, "FIN": 20.0,
    "FRA": 25.8, "DEU": 29.9, "GRC": 22.0, "HUN": 9.0, "IND": 25.2, "IDN": 22.0, "IRL": 12.5,
    "ISR": 23.0, "ITA": 27.8, "JPN": 29.7, "KEN": 30.0, "MYS": 24.0, "MEX": 30.0, "NLD": 25.8,
    "NZL": 28.0, "NGA": 30.0, "NOR": 22.0, "PER": 29.5, "PHL": 25.0, "POL": 19.0, "PRT": 31.5,
    "ROU": 16.0, "SGP": 17.0, "SVK": 21.0, "ZAF": 27.0, "KOR": 26.4, "ESP": 25.0, "SWE": 20.6,
    "CHE": 19.6, "THA": 20.0, "TUR": 25.0, "GBR": 25.0, "VNM": 20.0,
}
FALLBACK_TAX_LABEL = "Bundled OECD CTS snapshot (fallback – verify)"

# 10-year yields (%) from the case packet table (Trading Economics, Nov 6, 2026).
FALLBACK_YIELDS = {
    "Brazil": 15.2, "Colombia": 12.8, "Mexico": 9.1, "India": 7.2, "Poland": 6.2,
    "United Kingdom": 5.2, "Australia": 5.0, "United States": 4.9, "France": 4.8,
    "Slovakia": 4.2, "Portugal": 3.7, "Canada": 3.7, "Germany": 3.3, "Japan": 2.6,
    "China": 1.6, "Switzerland": 0.4,
}
FALLBACK_YIELD_LABEL = "Case packet table (Trading Economics, 2026-11-06) – fallback"

FALLBACK_SPREAD = 1.11  # %, Damodaran Baa2/BBB large non-financial, Jan 2026
FALLBACK_SPREAD_LABEL = "Damodaran, Jan 2026 (bundled fallback)"


# ---------------------------------------------------------------------------
# Core calculations (pure functions – tested in test_calculations.py)
# ---------------------------------------------------------------------------
def compute_cost_of_capital(
    beta_u: float,
    tax: float,
    debt_share: float,
    erp: float,
    rf_us: float,
    rf_f: float,
    spread_usd: float,
    spread_f_override: float | None = None,
) -> dict:
    """All rates as decimals (0.05 = 5%). Returns a dict of intermediate and final results."""
    if not 0 <= debt_share < 1:
        raise ValueError("Debt share must be in [0, 1).")
    de = debt_share / (1 - debt_share)
    beta_e = beta_u * (1 + (1 - tax) * de)

    re_usd = rf_us + beta_e * erp
    factor = (1 + rf_f) / (1 + rf_us)
    re_f = (1 + re_usd) * factor - 1

    rd_usd = rf_us + spread_usd
    spread_f_auto = spread_usd * factor
    spread_f = spread_f_auto if spread_f_override is None else spread_f_override
    rd_f = rf_f + spread_f

    rd_usd_at = rd_usd * (1 - tax)
    rd_f_at = rd_f * (1 - tax)
    wacc_usd = (1 - debt_share) * re_usd + debt_share * rd_usd_at
    wacc_f = (1 - debt_share) * re_f + debt_share * rd_f_at

    return dict(
        de=de, beta_e=beta_e, factor=factor,
        re_usd=re_usd, re_f=re_f,
        spread_usd=spread_usd, spread_f=spread_f, spread_f_auto=spread_f_auto,
        rd_usd=rd_usd, rd_f=rd_f, rd_usd_at=rd_usd_at, rd_f_at=rd_f_at,
        wacc_usd=wacc_usd, wacc_f=wacc_f,
    )


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_betas() -> dict:
    """Damodaran U.S. industry betas (sheet 'Industry Averages', column 'Unlevered beta')."""
    try:
        r = _get(BETAS_URL)
        sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None)
        raw = None
        for name, df in sheets.items():
            if "industry averages" in str(name).lower():
                raw = df
                break
        if raw is None:  # first sheet that has an 'Industry Name' header
            for df in sheets.values():
                if df.astype(str).apply(lambda c: c.str.strip().str.lower().eq("industry name")).any().any():
                    raw = df
                    break
        if raw is None:
            raise ValueError("Could not find the industry table in betas.xls")

        mask = raw.astype(str).apply(lambda c: c.str.strip().str.lower().eq("industry name"))
        hdr_row = mask.any(axis=1).idxmax()
        header = [str(h).strip() for h in raw.loc[hdr_row]]
        body = raw.loc[hdr_row + 1:].copy()
        body.columns = header

        ind_col = next(h for h in header if h.lower() == "industry name")
        lower = {h: h.lower() for h in header}
        ub_col = next((h for h in header if lower[h] == "unlevered beta"), None)
        if ub_col is None:
            ub_col = next(h for h in header if "unlevered beta" in lower[h] and "cash" not in lower[h])
        lev_col = next((h for h in header if lower[h] == "beta"), None)

        cols = [ind_col, ub_col] + ([lev_col] if lev_col else [])
        out = body[cols].rename(columns={ind_col: "Industry", ub_col: "Unlevered beta",
                                         **({lev_col: "Levered beta (Damodaran)"} if lev_col else {})})
        out["Industry"] = out["Industry"].astype(str).str.strip()
        out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
        out = out[out["Industry"].ne("nan") & out["Unlevered beta"].notna()]
        out = out[~out["Industry"].str.lower().str.startswith("total market")]
        out = out.drop_duplicates("Industry").reset_index(drop=True)

        # Data date: look in the rows above the header for a 'Date' label or a datetime cell
        as_of = None
        top = raw.loc[: hdr_row - 1]
        for _, row in top.iterrows():
            vals = list(row.values)
            for i, v in enumerate(vals):
                if isinstance(v, (datetime, pd.Timestamp)):
                    as_of = pd.Timestamp(v).strftime("%Y-%m-%d")
                elif isinstance(v, str) and "date" in v.lower():
                    m = re.search(r"([A-Z][a-z]+\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})", v)
                    if m:
                        as_of = m.group(1)
                    elif i + 1 < len(vals) and pd.notna(vals[i + 1]):
                        nxt = vals[i + 1]
                        as_of = (pd.Timestamp(nxt).strftime("%Y-%m-%d")
                                 if isinstance(nxt, (datetime, pd.Timestamp)) else str(nxt))
            if as_of:
                break
        if not as_of:
            as_of = r.headers.get("Last-Modified", "see source file")
        return dict(ok=True, data=out, as_of=as_of, fetched=_now(), error=None)
    except Exception as e:  # noqa: BLE001
        return dict(ok=False, data=None, as_of=None, fetched=_now(), error=str(e))


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_bbb_spread() -> dict:
    """Damodaran ratings page – Baa2/BBB spread, large non-financial service firms (left table)."""
    try:
        r = _get(RATINGS_URL)
        soup = BeautifulSoup(r.text, "lxml")
        spread = None
        for tr in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            for i, c in enumerate(cells):
                if re.sub(r"\s+", "", c).lower() == "baa2/bbb":
                    for nxt in cells[i + 1:]:
                        m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", nxt)
                        if m:
                            spread = float(m.group(1))
                            break
                    break
            if spread is not None:
                break  # first occurrence = large non-financial service firms
        if spread is None:
            raise ValueError("Baa2/BBB row not found")
        text = soup.get_text(" ", strip=True)
        m = re.search(r"as of\s+([A-Z][a-z]+\s+\d{4})", text)
        as_of = m.group(1) if m else r.headers.get("Last-Modified", "see source page")
        return dict(ok=True, value=spread, as_of=as_of, fetched=_now(), error=None)
    except Exception as e:  # noqa: BLE001
        return dict(ok=False, value=None, as_of=None, fetched=_now(), error=str(e))


def _parse_oecd_sdmx_csv(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text))
    cols = {c.upper(): c for c in df.columns}
    area = cols.get("REF_AREA") or next(c for c in df.columns if "REF_AREA" in c.upper() or c.upper() == "COU")
    time = cols.get("TIME_PERIOD")
    val = cols.get("OBS_VALUE")
    # keep the "combined" statutory rate series
    keep = pd.Series(False, index=df.index)
    for c in df.columns:
        s = df[c].astype(str)
        hit = s.str.upper().eq("COMB_CIT_RATE") | s.str.contains("Combined corporate income tax rate", case=False)
        if hit.any():
            keep |= hit
    if keep.any():
        df = df[keep]
    df = df[[area, time, val]].dropna()
    df.columns = ["iso3", "year", "rate"]
    df["year"] = pd.to_numeric(df["year"].astype(str).str[:4], errors="coerce")
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
    df = df.dropna().sort_values("year").groupby("iso3").tail(1)
    return df


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_tax_rates() -> dict:
    """OECD combined statutory CIT rates. Tries OECD SDMX API, then the publication page."""
    errors = []
    for url in OECD_SDMX_URLS:
        try:
            r = _get(url)
            df = _parse_oecd_sdmx_csv(r.text)
            if len(df) >= 10:
                rates = dict(zip(df["iso3"], df["rate"]))
                years = dict(zip(df["iso3"], df["year"].astype(int)))
                return dict(ok=True, rates=rates, years=years, source="OECD Corporate Tax Statistics (SDMX API)",
                            url=url.split("?")[0], fetched=_now(), error=None)
        except Exception as e:  # noqa: BLE001
            errors.append(f"SDMX: {e}")
    try:  # publication page (HTML table, if present)
        r = _get(OECD_PAGE_URL)
        tables = pd.read_html(io.StringIO(r.text))
        name_to_iso = {k.lower(): v[0] for k, v in COUNTRIES.items()}
        for alias_country, v in COUNTRIES.items():
            for a in v[3]:
                name_to_iso[a.lower()] = v[0]
        for t in tables:
            t = t.copy()
            t.columns = [str(c) for c in t.columns]
            first = t.columns[0]
            num_cols = [c for c in t.columns[1:] if pd.to_numeric(t[c], errors="coerce").notna().mean() > 0.5]
            if not num_cols:
                continue
            col = num_cols[-1]
            rates = {}
            for _, row in t.iterrows():
                iso = name_to_iso.get(str(row[first]).strip().lower())
                v = pd.to_numeric(row[col], errors="coerce")
                if iso and pd.notna(v):
                    rates[iso] = float(v)
            if len(rates) >= 10:
                yr = re.search(r"(20\d{2})", col)
                y = int(yr.group(1)) if yr else 2024
                return dict(ok=True, rates=rates, years={k: y for k in rates},
                            source="OECD Corporate Tax Statistics 2024 (publication page)",
                            url=OECD_PAGE_URL, fetched=_now(), error=None)
        errors.append("Page: no rate table found in HTML")
    except Exception as e:  # noqa: BLE001
        errors.append(f"Page: {e}")
    return dict(ok=False, rates={}, years={}, source=None, url=None, fetched=_now(), error="; ".join(errors))


def _norm_country(name: str) -> str | None:
    s = re.sub(r"\b(10Y|10-?year|10 ?yr|Bond Yield|Government Bond)\b", "", str(name), flags=re.I).strip()
    s_low = s.lower()
    for country, (_, _, _, aliases) in COUNTRIES.items():
        if s_low == country.lower() or s_low in (a.lower() for a in aliases):
            return country
    return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_bond_table() -> dict:
    """Trading Economics /bonds – all 10Y yields shown on the page."""
    try:
        r = _get(TE_BONDS_URL)
        tables = pd.read_html(io.StringIO(r.text))
        yields, dates = {}, {}
        for t in tables:
            t = t.copy()
            t.columns = [str(c[-1] if isinstance(c, tuple) else c).strip() for c in t.columns]
            name_col = t.columns[0]
            ycol = next((c for c in t.columns if c.lower() == "yield"), None)
            if ycol is None and len(t.columns) > 1:
                ycol = t.columns[1]
            dcol = next((c for c in t.columns if c.lower() == "date"), None)
            for _, row in t.iterrows():
                raw_name = str(row[name_col])
                if re.search(r"\b(\d{1,2}Y|\d+M)\b", raw_name) and not re.search(r"\b10Y\b", raw_name):
                    continue  # skip non-10Y maturities if labelled
                c = _norm_country(raw_name)
                v = pd.to_numeric(str(row[ycol]).replace("%", ""), errors="coerce")
                if c and pd.notna(v) and c not in yields:
                    yields[c] = float(v)
                    dates[c] = str(row[dcol]) if dcol else "latest"
        if len(yields) < 5:
            raise ValueError("Bond table not parsed (page layout may have changed)")
        return dict(ok=True, yields=yields, dates=dates, fetched=_now(), error=None)
    except Exception as e:  # noqa: BLE001
        return dict(ok=False, yields={}, dates={}, fetched=_now(), error=str(e))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_country_yield(country: str) -> dict:
    """Fallback: Trading Economics country page, e.g. /colombia/government-bond-yield."""
    slug = COUNTRIES[country][2]
    url = TE_COUNTRY_URL.format(slug=slug)
    try:
        r = _get(url)
        soup = BeautifulSoup(r.text, "lxml")
        meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
        text = (meta.get("content", "") if meta else "") + " " + soup.get_text(" ", strip=True)[:5000]
        m = re.search(r"(-?\d+(?:\.\d+)?)%\s+on\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", text)
        if not m:
            raise ValueError("yield sentence not found")
        return dict(ok=True, value=float(m.group(1)), date=m.group(2), url=url, fetched=_now(), error=None)
    except Exception as e:  # noqa: BLE001
        return dict(ok=False, value=None, date=None, url=url, fetched=_now(), error=str(e))


def get_yield(country: str, table: dict) -> tuple[float | None, str, str]:
    """Return (yield %, source label, as-of date)."""
    if table["ok"] and country in table["yields"]:
        return table["yields"][country], "Trading Economics – /bonds", table["dates"].get(country, "latest")
    single = fetch_country_yield(country)
    if single["ok"]:
        return single["value"], f"Trading Economics – {single['url']}", single["date"]
    if country in FALLBACK_YIELDS:
        return FALLBACK_YIELDS[country], FALLBACK_YIELD_LABEL, "2026-11-06"
    return None, "Unavailable – enter manually", "—"


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
def pct(x: float, nd: int = 4) -> str:
    return f"{x * 100:.{nd}f}%"


def override_input(label: str, auto_value: float | None, key: str, step: float = 0.01,
                   fmt: str = "%.2f", min_value: float = -5.0, max_value: float = 100.0,
                   help_text: str | None = None) -> tuple[float | None, bool]:
    """Checkbox + number input. Returns (value, is_manual)."""
    on = st.checkbox(f"Override {label}", key=f"ov_{key}", value=auto_value is None, help=help_text)
    default = float(auto_value) if auto_value is not None else 0.0
    if on:
        v = st.number_input(label, value=default, step=step, format=fmt, key=f"val_{key}",
                            min_value=min_value, max_value=max_value)
        return v, True
    st.caption(f"Auto: {fmt % auto_value}" if auto_value is not None else "Auto: unavailable")
    return auto_value, False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")
    st.title("🌍 International Cost of Capital")
    st.caption("Cost of equity, cost of debt and WACC for a project in USD and in the selected country's currency.")

    # ---- Data refresh ----
    with st.sidebar:
        st.header("Data")
        if st.button("🔄 Refresh data", width="stretch",
                     help="Clear cached data and re-download from all sources."):
            st.cache_data.clear()
            st.rerun()

    with st.spinner("Fetching market data…"):
        betas = fetch_betas()
        spread_res = fetch_bbb_spread()
        tax_res = fetch_tax_rates()
        bonds = fetch_bond_table()

    with st.sidebar:
        st.caption(f"Last fetch: {betas['fetched']}")

        # ---- Project inputs ----
        st.header("Project")
        if betas["ok"]:
            industries = betas["data"]["Industry"].tolist()
            idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
            industry = st.selectbox("Industry (Damodaran, U.S.)", industries, index=idx)
            beta_auto = float(betas["data"].loc[betas["data"]["Industry"] == industry, "Unlevered beta"].iloc[0])
        else:
            industry = DEFAULT_INDUSTRY
            st.warning("Industry betas unavailable – enter the unlevered beta manually below.")
            beta_auto = None

        names = list(COUNTRIES.keys())
        country = st.selectbox("Target country", names, index=names.index(DEFAULT_COUNTRY))
        iso_f, ccy_f = COUNTRIES[country][0], COUNTRIES[country][1]
        col_us = "USD (United States)"
        col_f = f"{ccy_f} ({country})" if country != "United States" else "USD (target: United States)"

        erp = st.slider("Equity risk premium (%)", 0.0, 15.0, DEFAULT_ERP, 0.1) / 100
        debt_share = st.slider("Target debt share D/(D+E) (%)", 0.0, 90.0, DEFAULT_DEBT, 1.0) / 100

        tax_choice = st.radio("Project tax rate", ["U.S. rate", f"{country} rate"], index=0, horizontal=True,
                              help="The same tax rate is applied in both currencies.")

        # ---- Overrides ----
        st.header("Manual overrides")
        with st.expander("Override fetched data", expanded=False):
            beta_u, beta_manual = override_input("unlevered beta", beta_auto, "beta", 0.01, "%.4f", 0.0, 5.0)

            tax_us_auto = tax_res["rates"].get("USA") if tax_res["ok"] else None
            tax_f_auto = tax_res["rates"].get(iso_f) if tax_res["ok"] else None
            tax_src_us = tax_src_f = (tax_res["source"] if tax_res["ok"] else None)
            if tax_us_auto is None:
                tax_us_auto, tax_src_us = FALLBACK_TAX.get("USA"), FALLBACK_TAX_LABEL
            if tax_f_auto is None:
                tax_f_auto, tax_src_f = FALLBACK_TAX.get(iso_f), FALLBACK_TAX_LABEL
            tax_us, tax_us_manual = override_input("U.S. corporate tax rate (%)", tax_us_auto, "tax_us", 0.1, "%.2f", 0.0, 60.0)
            if country != "United States":
                tax_f, tax_f_manual = override_input(f"{country} corporate tax rate (%)", tax_f_auto, "tax_f", 0.1, "%.2f", 0.0, 60.0)
            else:
                tax_f, tax_f_manual = tax_us, tax_us_manual

            spread_auto = spread_res["value"] if spread_res["ok"] else FALLBACK_SPREAD
            spread_src = "Damodaran ratings.html (Baa2/BBB, large non-financial)" if spread_res["ok"] else FALLBACK_SPREAD_LABEL
            spread_us, spread_manual = override_input("USD BBB spread (%)", spread_auto, "spread", 0.01, "%.2f", 0.0, 20.0)

            y_us_auto, y_us_src, y_us_date = get_yield("United States", bonds)
            y_us, y_us_manual = override_input("U.S. 10Y yield (%)", y_us_auto, "y_us", 0.01, "%.3f")
            if country != "United States":
                y_f_auto, y_f_src, y_f_date = get_yield(country, bonds)
                y_f, y_f_manual = override_input(f"{country} 10Y yield (%)", y_f_auto, "y_f", 0.01, "%.3f")
            else:
                y_f_auto, y_f_src, y_f_date = y_us_auto, y_us_src, y_us_date
                y_f, y_f_manual = y_us, y_us_manual

        with st.expander(f"{ccy_f} borrowing spread", expanded=False):
            sf_on = st.toggle(f"Override {ccy_f} spread", value=False, key="sf_on",
                              help="Off: s* = s × (1+Rf*)/(1+Rf), the same currency factor applied to the gross rate.")
            sf_auto = (spread_us * (1 + y_f / 100) / (1 + y_us / 100)
                       if None not in (spread_us, y_us, y_f) else None)
            if sf_on:
                sf_manual_val = st.number_input(f"{ccy_f} BBB spread (%)", value=float(sf_auto or 0.0), step=0.01,
                                                format="%.4f", key="sf_val", min_value=0.0, max_value=50.0)
            else:
                sf_manual_val = None
                st.caption(f"Auto calculation: s × (1+Rf*)/(1+Rf) = "
                           + (f"{sf_auto:.4f}%" if sf_auto is not None else "unavailable"))

    # ---- Validate inputs ----
    missing = [n for n, v in [("unlevered beta", beta_u), ("U.S. tax rate", tax_us), (f"{country} tax rate", tax_f),
                              ("USD spread", spread_us), ("U.S. yield", y_us), (f"{country} yield", y_f)] if v is None]
    if missing:
        st.error("Missing inputs – please enter manually in the sidebar: " + ", ".join(missing))
        st.stop()

    tax = (tax_us if tax_choice == "U.S. rate" else tax_f) / 100
    rf_us, rf_f, s_us = y_us / 100, y_f / 100, spread_us / 100
    s_f_override = sf_manual_val / 100 if (sf_on and sf_manual_val is not None) else None
    res = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_f, s_us, s_f_override)

    # ---- Headline results ----
    st.subheader(f"{industry} · {country}")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"#### {col_us}")
        a, b = st.columns(2)
        a.metric("Cost of equity", pct(res["re_usd"], 2))
        b.metric("WACC", pct(res["wacc_usd"], 2))
    with c2:
        st.markdown(f"#### {col_f}")
        a, b = st.columns(2)
        a.metric("Cost of equity", pct(res["re_f"], 2), delta=f"{(res['re_f'] - res['re_usd']) * 1e4:+.0f} bp vs USD",
                 delta_color="off")
        b.metric("WACC", pct(res["wacc_f"], 2), delta=f"{(res['wacc_f'] - res['wacc_usd']) * 1e4:+.0f} bp vs USD",
                 delta_color="off")

    results = pd.DataFrame(
        {
            col_us: [rf_us, res["beta_e"], res["re_usd"], res["spread_usd"], res["rd_usd"], res["rd_usd_at"],
                    1 - debt_share, debt_share, res["wacc_usd"]],
            col_f: [rf_f, res["beta_e"], res["re_f"], res["spread_f"], res["rd_f"], res["rd_f_at"],
                    1 - debt_share, debt_share, res["wacc_f"]],
        },
        index=["Risk-free rate (10Y govt yield)", "Levered (equity) beta", "Cost of equity",
               "BBB borrowing spread", "Pretax cost of debt", "After-tax cost of debt",
               "Equity weight", "Debt weight", "WACC"],
    )
    disp = results.copy().astype(object)
    for idx in disp.index:
        for col in disp.columns:
            v = results.loc[idx, col]
            disp.loc[idx, col] = f"{v:.4f}" if idx == "Levered (equity) beta" else pct(v, 4)
    st.dataframe(disp, width="stretch")
    st.caption(
        f"Shared inputs: unlevered beta {beta_u:.4f} · tax rate {tax:.2%} ({tax_choice}) · debt share {debt_share:.0%} "
        f"· ERP {erp:.2%} · currency factor F = (1+Rf*)/(1+Rf) = {res['factor']:.6f}"
        + (f" · {ccy_f} spread: manual" if sf_on else f" · {ccy_f} spread: auto = s × F = {pct(res['spread_f_auto'], 4)}")
    )

    tabs = st.tabs(["📐 Calculation steps", "🏭 All industries", "📚 Sources & data", "🧭 Assumptions", "✅ Checks"])

    # ---- Calculation steps ----
    with tabs[0]:
        st.markdown("**1. Relever the industry beta at the project's debt share and tax rate**")
        st.latex(r"\tfrac{D}{E}=\tfrac{d}{1-d}=" + f"{res['de']:.4f}" +
                 r"\qquad \beta_E=\beta_U\,[1+(1-\tau)\tfrac{D}{E}]=" +
                 f"{beta_u:.4f}\\times[1+{1 - tax:.4f}\\times{res['de']:.4f}]={res['beta_e']:.4f}")
        st.markdown("**2. Cost of equity in USD (CAPM, U.S. yield as risk-free proxy)**")
        st.latex(r"R_e=R_f+\beta_E\times ERP=" + f"{rf_us:.4%}+{res['beta_e']:.4f}\\times{erp:.2%}={res['re_usd']:.4%}".replace("%", r"\%"))
        st.markdown(f"**3. Cost of equity in {ccy_f} (Fisher equation + relative PPP)**")
        st.latex(r"1+R_e^*=(1+R_e)\frac{1+R_f^*}{1+R_f}\;\Rightarrow\;R_e^*=" +
                 f"(1+{res['re_usd']:.4%})\\times{res['factor']:.6f}-1={res['re_f']:.4%}".replace("%", r"\%"))
        st.markdown("**4. Pretax cost of debt**")
        st.latex(r"R_d=R_f+s=" + f"{rf_us:.4%}+{s_us:.4%}={res['rd_usd']:.4%}".replace("%", r"\%"))
        if sf_on:
            st.latex(r"R_d^*=R_f^*+s^*_{\text{manual}}=" + f"{rf_f:.4%}+{res['spread_f']:.4%}={res['rd_f']:.4%}".replace("%", r"\%"))
        else:
            st.latex(r"s^*=s\,\frac{1+R_f^*}{1+R_f}=" + f"{res['spread_f']:.4%}".replace("%", r"\%") +
                     r"\qquad R_d^*=R_f^*+s^*=" + f"{res['rd_f']:.4%}".replace("%", r"\%"))
        st.markdown("**5. WACC (same debt share and tax rate in both currencies)**")
        st.latex(r"WACC=(1-d)R_e+d\,R_d(1-\tau)=" +
                 f"{1 - debt_share:.2f}\\times{res['re_usd']:.4%}+{debt_share:.2f}\\times{res['rd_usd_at']:.4%}={res['wacc_usd']:.4%}".replace("%", r"\%"))
        st.latex(r"WACC^*=" +
                 f"{1 - debt_share:.2f}\\times{res['re_f']:.4%}+{debt_share:.2f}\\times{res['rd_f_at']:.4%}={res['wacc_f']:.4%}".replace("%", r"\%"))

    # ---- All industries ----
    with tabs[1]:
        if betas["ok"]:
            rows = []
            for _, r_ in betas["data"].iterrows():
                o = compute_cost_of_capital(float(r_["Unlevered beta"]), tax, debt_share, erp, rf_us, rf_f, s_us, s_f_override)
                rows.append({"Industry": r_["Industry"], "Unlevered beta": r_["Unlevered beta"],
                             "Levered beta": o["beta_e"],
                             f"Cost of equity – {col_us}": o["re_usd"], f"Cost of equity – {col_f}": o["re_f"],
                             f"WACC – {col_us}": o["wacc_usd"], f"WACC – {col_f}": o["wacc_f"]})
            all_ind = pd.DataFrame(rows)
            sort_col = st.selectbox("Sort by", list(all_ind.columns[1:]), index=4)
            all_ind = all_ind.sort_values(sort_col, ascending=False).reset_index(drop=True)
            st.caption(f"Same country ({country}), tax rate, debt share, ERP, yields and spreads for every industry. "
                       f"Selected industry highlighted.")
            fmt = {c: "{:.2%}" for c in all_ind.columns if "Cost" in c or "WACC" in c}
            fmt.update({"Unlevered beta": "{:.2f}", "Levered beta": "{:.2f}"})
            styled = all_ind.style.format(fmt).apply(
                lambda r_: ["background-color: rgba(255,200,0,0.25)" if r_["Industry"] == industry else "" for _ in r_],
                axis=1)
            st.dataframe(styled, width="stretch", height=420)
            chart_df = all_ind.set_index("Industry")[[f"WACC – {col_us}", f"WACC – {col_f}"]] * 100
            st.markdown("**WACC by industry (%)**")
            st.bar_chart(chart_df, height=420, stack=False)
        else:
            all_ind = None
            st.info("All-industry comparison needs the Damodaran beta file. Try **Refresh data**.")

    # ---- Sources ----
    with tabs[2]:
        def status(manual: bool, ok: bool) -> str:
            return "✏️ Manual" if manual else ("🟢 Live" if ok else "🟠 Fallback")

        src = pd.DataFrame([
            ["Unlevered beta – " + industry, f"{beta_u:.4f}", "Damodaran, U.S. industry betas (betas.xls)",
             betas["as_of"] or "—", status(beta_manual, betas["ok"]), BETAS_URL],
            ["U.S. corporate tax rate", f"{tax_us:.2f}%", tax_src_us or FALLBACK_TAX_LABEL,
             str(tax_res["years"].get("USA", "—")) if tax_res["ok"] else "snapshot",
             status(tax_us_manual, tax_res["ok"] and "USA" in tax_res["rates"]), tax_res["url"] or OECD_PAGE_URL],
            [f"{country} corporate tax rate", f"{tax_f:.2f}%", tax_src_f or FALLBACK_TAX_LABEL,
             str(tax_res["years"].get(iso_f, "—")) if tax_res["ok"] else "snapshot",
             status(tax_f_manual, tax_res["ok"] and iso_f in tax_res["rates"]), tax_res["url"] or OECD_PAGE_URL],
            ["USD BBB spread (Baa2/BBB)", f"{spread_us:.2f}%", spread_src, spread_res["as_of"] or "Jan 2026",
             status(spread_manual, spread_res["ok"]), RATINGS_URL],
            ["U.S. 10Y government yield", f"{y_us:.3f}%", y_us_src, y_us_date,
             status(y_us_manual, "fallback" not in y_us_src.lower() and y_us_auto is not None), TE_BONDS_URL],
            [f"{country} 10Y government yield", f"{y_f:.3f}%", y_f_src, y_f_date,
             status(y_f_manual, "fallback" not in y_f_src.lower() and y_f_auto is not None), TE_BONDS_URL],
            ["Equity risk premium", f"{erp:.2%}", "User assumption (default 6.5%)", "—", "✏️ User", ""],
            ["Target debt share", f"{debt_share:.0%}", "User assumption (default 30%)", "—", "✏️ User", ""],
        ], columns=["Input", "Value", "Source", "Data date", "Status", "Link"])
        st.dataframe(src, width="stretch", hide_index=True,
                     column_config={"Link": st.column_config.LinkColumn("Link")})
        st.caption(f"Fetched: {betas['fetched']}. Data are cached for up to 24h (yields 6h); use **Refresh data** to re-download.")
        errs = {k: v["error"] for k, v in [("Betas", betas), ("BBB spread", spread_res), ("Tax rates", tax_res),
                                           ("Bond table", bonds)] if v.get("error")}
        if errs:
            with st.expander("Fetch diagnostics"):
                for k, v in errs.items():
                    st.write(f"**{k}:** {v}")

    # ---- Assumptions ----
    with tabs[3]:
        st.markdown(
            """
- **Risk benchmark:** risk is measured against the U.S. market (CAPM with U.S. 10-year yield and a market ERP); no separate country equity risk premium.
- **Project risk:** Damodaran's U.S. industry unlevered beta proxies for project risk; it is relevered at the project's own target debt share and tax rate, assuming debt and the tax shield have zero beta: βE = βU [1 + (1 − τ) D/E].
- **Currency conversion:** equal real rates (Fisher) and relative PPP imply (1 + R*) = (1 + R)(1 + Rf*)/(1 + Rf). Currency risk is treated as diversifiable (FX betas near zero), so there's no extra currency premium.
- **Government yields as risk-free proxies:** they include liquidity/term premia and may embed sovereign default risk.
- **Debt cost:** USD pretax cost = U.S. yield + BBB spread. By default the local spread scales by the same currency factor, s* = s (1 + Rf*)/(1 + Rf), so the gross pretax rate converts exactly like equity. The local spread can be overridden.
- **Financing and taxes:** constant target leverage, interest fully and immediately deductible, the **same** tax rate (U.S. or selected country), debt share and ERP in both currencies; interest is deducted in each financing currency.
- **Precision:** calculations use full precision; displayed rates are rounded.
"""
        )

    # ---- Built-in checks ----
    with tabs[4]:
        st.markdown("Live sanity checks using the current inputs:")
        eq = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_us, s_us, None)
        z = compute_cost_of_capital(beta_u, tax, 0.0, erp, rf_us, rf_f, s_us, s_f_override)
        ov = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_f, s_us, (s_us * 3))
        noov = compute_cost_of_capital(beta_u, tax, debt_share, erp, rf_us, rf_f, s_us, None)
        checks = pd.DataFrame([
            ["Equal yields (Rf* = Rf, override off) → equal results",
             abs(eq["re_usd"] - eq["re_f"]) < 1e-12 and abs(eq["wacc_usd"] - eq["wacc_f"]) < 1e-12,
             f"Re {pct(eq['re_usd'])} vs {pct(eq['re_f'])}; WACC {pct(eq['wacc_usd'])} vs {pct(eq['wacc_f'])}"],
            ["Zero debt → WACC = cost of equity (both currencies)",
             abs(z["wacc_usd"] - z["re_usd"]) < 1e-12 and abs(z["wacc_f"] - z["re_f"]) < 1e-12,
             f"USD {pct(z['wacc_usd'])} = {pct(z['re_usd'])}; {ccy_f} {pct(z['wacc_f'])} = {pct(z['re_f'])}"],
            [f"Overriding the {ccy_f} spread leaves USD results unchanged",
             all(abs(ov[k] - noov[k]) < 1e-12 for k in ["re_usd", "rd_usd", "wacc_usd"]),
             f"USD WACC {pct(noov['wacc_usd'])} (auto) vs {pct(ov['wacc_usd'])} (override = 3×s)"],
        ], columns=["Check", "Pass", "Detail"])
        checks["Pass"] = checks["Pass"].map({True: "✅", False: "❌"})
        st.dataframe(checks, width="stretch", hide_index=True)

    # ---- Downloads ----
    st.divider()
    st.subheader("⬇️ Download results")
    inputs_df = pd.DataFrame({
        "Input": ["Industry", "Country", "Currency", "Unlevered beta", "Tax rate used", "Tax choice",
                  "U.S. tax rate", f"{country} tax rate", "Debt share", "ERP", "U.S. 10Y yield",
                  f"{country} 10Y yield", "USD BBB spread", f"{ccy_f} spread", f"{ccy_f} spread mode", "Fetched"],
        "Value": [industry, country, ccy_f, beta_u, tax, tax_choice, tax_us / 100, tax_f / 100, debt_share, erp,
                  rf_us, rf_f, s_us, res["spread_f"], "manual" if sf_on else "auto (s × F)", betas["fetched"]],
    })
    d1, d2, d3 = st.columns(3)
    out_csv = results.reset_index().rename(columns={"index": "Item"})
    d1.download_button("Results (CSV)", out_csv.to_csv(index=False).encode(), "cost_of_capital_results.csv",
                       "text/csv", width="stretch")
    if all_ind is not None:
        d2.download_button("All industries (CSV)", all_ind.to_csv(index=False).encode(),
                           "cost_of_capital_all_industries.csv", "text/csv", width="stretch")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs_df.to_excel(xw, sheet_name="Inputs", index=False)
        out_csv.to_excel(xw, sheet_name="Results", index=False)
        if all_ind is not None:
            all_ind.to_excel(xw, sheet_name="All industries", index=False)
        src.to_excel(xw, sheet_name="Sources", index=False)
    d3.download_button("Everything (Excel)", buf.getvalue(), "cost_of_capital.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")


if __name__ == "__main__":
    main()
