import streamlit as st
import pandas as pd
import numpy as np
import requests

st.set_page_config(page_title="International Cost of Capital Calculator", layout="wide")

@st.cache_data
def load_betas():
    try:
        url = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
        df = pd.read_excel(url, sheet_name=0, header=None)
        header_row = -1
        for i, row in df.iterrows():
            if 'Industry Name' in row.values or 'Industry name' in row.values:
                header_row = i
                break
        if header_row != -1:
            df = pd.read_excel(url, sheet_name=0, skiprows=header_row)
        df.columns = [str(c).strip() for c in df.columns]
        df = df[['Industry Name', 'Unlevered beta']].dropna(subset=['Industry Name'])
        df['Unlevered beta'] = pd.to_numeric(df['Unlevered beta'], errors='coerce')
        return df.dropna()
    except Exception as e:
        return pd.DataFrame({'Industry Name': ['Software (System & Application)'], 'Unlevered beta': [1.22]})

@st.cache_data
def load_spreads():
    try:
        url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
        dfs = pd.read_html(url)
        for df in dfs:
            for idx, row in df.iterrows():
                row_str = " ".join([str(x) for x in row.values])
                if 'Baa2/BBB' in row_str or 'BBB' in row_str:
                    for val in row.values:
                        if isinstance(val, str) and '%' in val:
                            return float(val.replace('%', '').strip())
        return 1.50
    except:
        return 1.50

@st.cache_data
def load_bonds():
    try:
        url = "https://tradingeconomics.com/bonds"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers)
        dfs = pd.read_html(res.text)
        bonds = pd.concat(dfs)
        if 'Country' in bonds.columns and 'Yield' in bonds.columns:
            bonds = bonds[['Country', 'Yield']].dropna()
            bonds['Yield'] = pd.to_numeric(bonds['Yield'], errors='coerce')
            if not 'United States' in bonds['Country'].values:
                bonds.loc[len(bonds)] = ['United States', 4.0]
            return bonds.dropna()
    except:
        pass
    return pd.DataFrame({
        'Country': ['United States', 'Colombia', 'Brazil', 'Mexico', 'India', 'United Kingdom'],
        'Yield': [4.0, 12.0, 15.2, 9.1, 7.2, 5.2]
    })

@st.cache_data
def load_taxes():
    return pd.DataFrame({
        'Country': ['United States', 'France', 'Argentina', 'Germany', 'Australia', 
                    'Japan', 'Colombia', 'Brazil', 'China', 'United Kingdom', 'Ireland'],
        'Tax Rate': [25.8, 36.1, 35.0, 30.1, 30.0, 29.7, 35.0, 34.0, 25.0, 25.0, 12.5]
    })

st.title("International Cost of Capital Calculator")
st.markdown("Compares project Cost of Equity and WACC in USD and a Foreign Currency.")

if st.button("Refresh Data Fetch"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.markdown("### Data Sources & Dates")
st.sidebar.markdown("- **Betas & Spreads:** Damodaran (Latest)\n- **Corporate Tax:** OECD (2024)\n- **Bond Yields:** Trading Economics (Live)")

df_betas = load_betas()
usd_spread_val = load_spreads()
df_bonds = load_bonds()
df_taxes = load_taxes()

st.sidebar.header("Global Parameters")
industry_list = df_betas['Industry Name'].tolist()
default_ind_idx = industry_list.index('Software (System & Application)') if 'Software (System & Application)' in industry_list else 0
selected_industry = st.sidebar.selectbox("Industry", industry_list, index=default_ind_idx)

country_list = df_bonds['Country'].unique().tolist()
default_country_idx = country_list.index('United States') if 'United States' in country_list else 0
selected_country = st.sidebar.selectbox("Target Country", country_list, index=default_country_idx)

erp_input = st.sidebar.number_input("Equity Risk Premium (ERP) %", value=6.5, step=0.1)
debt_share_input = st.sidebar.number_input("Target Debt Share %", value=30.0, step=1.0, min_value=0.0, max_value=99.9)

tax_choice = st.sidebar.radio("Project Tax Rate Source", ["U.S. Tax Rate", f"{selected_country} Tax Rate"])

st.header("Inputs & Manual Overrides")
enable_overrides = st.checkbox("Enable Manual Overrides", value=False)

default_beta_u = float(df_betas[df_betas['Industry Name'] == selected_industry]['Unlevered beta'].values[0])
us_bond_val = float(df_bonds[df_bonds['Country'] == 'United States']['Yield'].values[0]) if 'United States' in df_bonds['Country'].values else 4.0
for_bond_val = float(df_bonds[df_bonds['Country'] == selected_country]['Yield'].values[0]) if selected_country in df_bonds['Country'].values else 10.0

us_tax_val = float(df_taxes[df_taxes['Country'] == 'United States']['Tax Rate'].values[0]) if 'United States' in df_taxes['Country'].values else 25.8
for_tax_val = float(df_taxes[df_taxes['Country'] == selected_country]['Tax Rate'].values[0]) if selected_country in df_taxes['Country'].values else 25.0

active_tax_default = us_tax_val if tax_choice == "U.S. Tax Rate" else for_tax_val

col1, col2, col3 = st.columns(3)
with col1:
    beta_u = st.number_input("Unlevered Beta", value=default_beta_u, disabled=not enable_overrides)
    tax_rate = st.number_input("Corporate Tax Rate %", value=active_tax_default, disabled=not enable_overrides)
with col2:
    usd_spread = st.number_input("USD BBB Spread %", value=usd_spread_val, disabled=not enable_overrides)
    us_yield = st.number_input("U.S. 10Y Gov Yield %", value=us_bond_val, disabled=not enable_overrides)
with col3:
    foreign_yield = st.number_input(f"{selected_country} 10Y Gov Yield %", value=for_bond_val, disabled=not enable_overrides)
    override_foreign_spread = st.checkbox("Override Foreign BBB Spread", value=False, disabled=not enable_overrides)
    
    auto_foreign_spread = usd_spread * (1 + foreign_yield/100.0) / (1 + us_yield/100.0)
    manual_foreign_spread = st.number_input("Foreign BBB Spread %", value=auto_foreign_spread, disabled=not (enable_overrides and override_foreign_spread))

d = debt_share_input / 100.0
tau = tax_rate / 100.0
erp = erp_input / 100.0
rf_us = us_yield / 100.0
rf_for = foreign_yield / 100.0
spread_us = usd_spread / 100.0

DE = d / (1 - d) if d < 1 else 0
beta_e = beta_u * (1 + (1 - tau) * DE)

re_usd = rf_us + beta_e * erp
rd_usd = rf_us + spread_us
rd_after_tax_usd = rd_usd * (1 - tau)
wacc_usd = (1 - d) * re_usd + d * rd_after_tax_usd

re_for = (1 + re_usd) * (1 + rf_for) / (1 + rf_us) - 1
spread_for = manual_foreign_spread / 100.0 if (enable_overrides and override_foreign_spread) else spread_us * (1 + rf_for) / (1 + rf_us)

rd_for = rf_for + spread_for
rd_after_tax_for = rd_for * (1 - tau)
wacc_for = (1 - d) * re_for + d * rd_after_tax_for

st.header("Cost of Capital Comparison")
colA, colB = st.columns(2)
with colA:
    st.subheader("USD (U.S.)")
    st.metric("Cost of Equity", f"{re_usd*100:.2f}%")
    st.metric("Pre-tax Cost of Debt", f"{rd_usd*100:.2f}%")
    st.metric("After-tax Cost of Debt", f"{rd_after_tax_usd*100:.2f}%")
    st.metric("WACC", f"{wacc_usd*100:.2f}%")
    
with colB:
    st.subheader(f"Local Currency ({selected_country})")
    st.metric("Cost of Equity", f"{re_for*100:.2f}%")
    st.metric("Pre-tax Cost of Debt", f"{rd_for*100:.2f}%")
    st.metric("After-tax Cost of Debt", f"{rd_after_tax_for*100:.2f}%")
    st.metric("WACC", f"{wacc_for*100:.2f}%")

st.markdown("### Main Assumptions")
st.markdown('''
- **Risk benchmark**: Risk is measured relative to the U.S. market.
- **Project risk**: U.S. industry betas proxy for project risk.
- **Currency conversion**: Equal real rates and relative PPP link yields to expected depreciation.
- **Financing and taxes**: Target leverage stays constant, interest deductions are fully usable, and unlevering assumes zero debt beta.
''')

st.header("All-Industry Comparison")
comparison_data = []
for idx, row in df_betas.iterrows():
    b_u = row['Unlevered beta']
    ind = row['Industry Name']
    b_e_temp = b_u * (1 + (1 - tau) * DE)
    ind_re_usd = rf_us + b_e_temp * erp
    ind_wacc_usd = (1 - d) * ind_re_usd + d * rd_after_tax_usd
    ind_re_for = (1 + ind_re_usd) * (1 + rf_for) / (1 + rf_us) - 1
    ind_wacc_for = (1 - d) * ind_re_for + d * rd_after_tax_for
    
    comparison_data.append({
        'Industry': ind,
        'Unlevered Beta': b_u,
        'Levered Beta': b_e_temp,
        'USD Cost of Equity (%)': ind_re_usd * 100,
        'USD WACC (%)': ind_wacc_usd * 100,
        f'{selected_country} Cost of Equity (%)': ind_re_for * 100,
        f'{selected_country} WACC (%)': ind_wacc_for * 100
    })

df_comparison = pd.DataFrame(comparison_data)
st.dataframe(df_comparison.style.format(precision=2))

st.download_button(
    label="Download Comparison as CSV",
    data=df_comparison.to_csv(index=False),
    file_name="wacc_comparison.csv",
    mime="text/csv",
)
