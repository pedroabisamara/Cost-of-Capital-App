# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of a selected country**, following the method in *International Valuation* (Rebelo, 2026).

**Live app:** `<your Streamlit URL>`

## Features
- **Live data** with sources, dates and a 🔄 *Refresh data* button:
  - U.S. industry **unlevered betas** – Damodaran, [`betas.xls`](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls)
  - **BBB (Baa2) USD spread**, large non-financial service firms – Damodaran, [`ratings.html`](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html)
  - **Combined statutory corporate tax rates** – OECD Corporate Tax Statistics (SDMX API, falling back to the [2024 publication page](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html))
  - **10-year government yields** – [Trading Economics /bonds](https://tradingeconomics.com/bonds), falling back to each country's `/government-bond-yield` page
- Select **industry** and **target country**; adjust **ERP** and **target debt share**.
- Switch the project **tax rate** between the U.S. rate and the selected country's rate (the same rate applies in both currencies).
- **Manual overrides** for unlevered beta, both tax rates, USD BBB spread and both yields. The **local-currency spread** can be overridden; when the override is off, the app shows the automatic value.
- Side-by-side results, step-by-step formulas, assumptions, **all-industry comparison** (table and chart), built-in sanity checks, and **CSV/Excel downloads**.
- **Defaults:** Software (System & Application), United States, U.S. tax rate, ERP 6.5%, debt share 30%.

## Method
| Step | Formula |
|---|---|
| Relever beta | βE = βU [1 + (1 − τ) d/(1 − d)] |
| Cost of equity (USD) | Re = Rf + βE × ERP |
| Currency factor | F = (1 + Rf\*) / (1 + Rf) |
| Cost of equity (local) | 1 + Re\* = (1 + Re) F |
| Cost of debt (USD) | Rd = Rf + s |
| Local spread (default) | s\* = s × F → Rd\* = Rf\* + s\* |
| WACC | (1 − d) Re + d Rd (1 − τ), in each currency |

Key assumptions: U.S. market as risk benchmark, no country equity premium, Fisher equation + relative PPP for currency conversion, zero debt beta, constant target leverage, fully usable interest deductions.

## Verification
`python -m pytest -q` runs `test_calculations.py`:
1. Reproduces the course's **Colombia example** (USD WACC 8.5265%, COP WACC 16.0670%).
2. **Equal government yields → equal results** (spread override off).
3. **Zero debt → WACC = cost of equity.**
4. **Overriding the local spread leaves USD results unchanged.**

The same checks run live in the app's **✅ Checks** tab.

## Data fallbacks
Scraped websites change. If a fetch fails, the app tells you which source failed (*Sources & data → Fetch diagnostics*) and uses a clearly labelled fallback: the case-packet yields (Nov 6, 2026), a bundled OECD tax snapshot, or Damodaran's January 2026 BBB spread. You can override any input by hand.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy
Push `app.py`, `requirements.txt` and `README.md` to a public GitHub repo → [share.streamlit.io](https://share.streamlit.io) → *New app* → select the repo, branch `main`, file `app.py` → *Deploy*.

*Built with AI assistance; calculations were verified independently.*
