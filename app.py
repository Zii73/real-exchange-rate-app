Monthly IMF bilateral real exchange rate explorer. Python 3.12.
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from io import StringIO
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st

BASE = 'https://api.imf.org/external/sdmx/3.0'
FIRST = '1976-01'
CSV_ACCEPT = 'application/vnd.sdmx.data+csv;version=2.0'


def get(url, accept):
    with requests.Session() as session:
        session.mount('https://', HTTPAdapter(max_retries=Retry(
            total=2, backoff_factor=0.8, status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['GET'])))
        response = session.get(url, headers={'Accept': accept}, timeout=(10, 60))
        if response.status_code in (204, 404):
            return None
        response.raise_for_status()
        return response


@st.cache_data(ttl=86400, show_spinner=False)
def countries():
    response = get(f'{BASE}/structure/dataflow/IMF.STA/CPI/+?references=all',
                   'application/json')
    if response is None:
        raise ValueError('IMF country metadata is unavailable.')
    lists = response.json()['data']['codelists']
    codes = next(c['codes'] for c in lists if c['id'] == 'CL_COUNTRY')
    result = {c['id']: c.get('names', {}).get('en', c.get('name', c['id']))
              for c in codes if re.fullmatch(r'[A-Z]{3}', c['id'])
              and c['id'] not in {'AFR', 'APD', 'EUR', 'MCD', 'WHD', 'SDS'}}
    result['G163'] = 'Euro area'
    return dict(sorted(result.items(), key=lambda item: item[1]))


def parse_csv(text, code, flow):
    df = pd.read_csv(StringIO(text), dtype=str)
    df.columns = df.columns.str.strip().str.upper()
    required = {'COUNTRY', 'FREQUENCY', 'TIME_PERIOD', 'OBS_VALUE'}
    if not required.issubset(df.columns):
        raise ValueError('Unexpected IMF response: missing observation columns.')
    df = df.loc[(df.COUNTRY == code) & (df.FREQUENCY == 'M')].copy()
    filters = {'INDEX_TYPE': 'CPI', 'COICOP_1999': '_T',
               'TYPE_OF_TRANSFORMATION': 'IX'} if flow == 'CPI' else {
               'INDICATOR': 'XDC_USD', 'TYPE_OF_TRANSFORMATION': 'PA_RT'}
    for col, value in filters.items():
        if col not in df:
            raise ValueError(f'IMF response missing dimension {col}.')
        df = df.loc[df[col] == value]
    df['month'] = pd.to_datetime(df.TIME_PERIOD.str.replace(
        r'^(\d{4})-?M(\d{2})$', r'\1-\2', regex=True), format='%Y-%m', errors='coerce')
    df = df.dropna(subset=['month'])
    if df.month.duplicated().any():
        raise ValueError('IMF returned multiple observations per month; refusing to average distinct series.')
    values = pd.to_numeric(df.OBS_VALUE, errors='coerce')
    scale = pd.to_numeric(df['SCALE'], errors='coerce').fillna(0) if 'SCALE' in df else 0
    df['value'] = values * np.power(10.0, scale)
    # Exclude zero, negative and non-finite observations; preserve their months as gaps.
    df['value'] = df.value.where(np.isfinite(df.value) & (df.value > 0))
    keep = ['value']
    for col in ['STATUS', 'REFERENCE_PERIOD', 'COMMON_REFERENCE_PERIOD', 'EXRATE', 'UNIT']:
        if col in df:
            keep.append(col)
    df = df.set_index('month')[keep].sort_index()
    return df.loc[df.index >= pd.Timestamp(FIRST)]


@st.cache_data(ttl=21600, show_spinner=False)
def series(code, flow):
    key = f'{code}.CPI._T.IX.M' if flow == 'CPI' else f'{code}.XDC_USD.PA_RT.M'
    url = f'{BASE}/data/dataflow/IMF.STA/{flow}/+/{key}'
    response = get(url, CSV_ACCEPT)
    frame = pd.DataFrame(columns=['value'], index=pd.DatetimeIndex([], name='month')) if response is None else parse_csv(response.text, code, flow)
    return frame, url, datetime.now(timezone.utc).isoformat()


def calculate(inputs, start, end):
    months = pd.date_range(start, end, freq='MS', name='month')
    frame = pd.DataFrame(index=months)
    for name, source in inputs.items():
        frame = frame.join(source.add_prefix(name + '_'))
    frame['S_C2_per_C1'] = frame['FX2_value'] / frame['FX1_value']
    frame['RER'] = frame.S_C2_per_C1 * frame.CPI1_value / frame.CPI2_value
    frame['RER'] = frame.RER.where(np.isfinite(frame.RER) & (frame.RER > 0))
    average = frame.RER.mean()
    frame['valid_RER'] = frame.RER.notna()
    frame['RER_star'] = average
    frame['Real_Index'] = 100 * frame.RER / average
    return frame, average


def main():
    st.set_page_config(page_title='Real Exchange Rate Explorer', page_icon='🌐', layout='wide')
    st.title('Real Exchange Rate Explorer')
    st.caption('Monthly IMF data · January 1976 onward where available')
    try:
        options = countries()
    except (requests.RequestException, ValueError, KeyError, StopIteration) as exc:
        st.error(f'Unable to load IMF country list: {exc}. Try again later.')
        st.stop()
    codes = list(options)
    label = lambda code: f'{options[code]} ({code})'
    a, b = st.columns(2)
    c1 = a.selectbox('Country 1', codes, index=codes.index('USA'), format_func=label)
    c2 = b.selectbox('Country 2', codes, index=codes.index('CHN'), format_func=label)
    st.info(f'S(t) = units of {options[c2]} currency per 1 unit of {options[c1]} currency. '
            'A higher real index indicates real appreciation of Country 1 relative to Country 2.')
    if st.button('Refresh IMF data'):
        st.cache_data.clear()
        st.rer_refresh = True
        st.rerun()
    inputs, provenance, errors = {}, [], []
    jobs = [('CPI1', c1, 'CPI'), ('CPI2', c2, 'CPI'), ('FX1', c1, 'ER'), ('FX2', c2, 'ER')]
    with st.spinner('Loading monthly IMF observations…'):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [(name, code, flow, pool.submit(series, code, flow)) for name, code, flow in jobs]
            for name, code, flow, future in futures:
                try:
                    frame, url, fetched = future.result()
                    inputs[name] = frame
                    provenance.append({'series': name, 'country': code, 'dataset': flow,
                                       'url': url, 'retrieved_UTC': fetched})
                except (requests.RequestException, ValueError, KeyError, pd.errors.ParserError) as exc:
                    errors.append(f'{name} ({code}): {exc}')
    if errors:
        st.error('IMF retrieval failed. No substitute or simulated data is used.')
        for error in errors:
            st.write(error)
        st.stop()
    today = pd.Timestamp(date.today()).replace(day=1)
    months = pd.date_range(FIRST, today, freq='MS')
    labels = months.strftime('%Y-%m').tolist()
    left, right, view_col = st.columns([1, 1, 1])
    start = left.selectbox('Start month', labels, index=0)
    end = right.selectbox('End month', labels, index=len(labels)-1)
    view = view_col.radio('Chart', ['Real', 'Nominal', 'Both'], horizontal=True)
    if start > end:
        st.error('Start month must be no later than end month.')
        st.stop()
    sample, average = calculate(inputs, start, end)
    count = int(sample.valid_RER.sum())
    m1, m2, m3 = st.columns(3)
    m1.metric('Valid RER months', f'{count} / {len(sample)}')
    m2.metric('Sample average RER*', f'{average:.6g}' if count else 'Unavailable')
    m3.metric('Missing RER months', len(sample)-count)
    if not count:
        st.warning('No jointly valid CPI and exchange-rate observations in this sample. Try different dates or countries.')
    elif count < len(sample):
        st.warning('Coverage is incomplete. Missing months stay blank and are excluded from RER*.')
    fig = make_subplots(specs=[[{'secondary_y': True}]])
    if view in ('Real', 'Both'):
        fig.add_trace(go.Scatter(x=sample.index, y=sample.Real_Index, name='Real index',
            line={'color':'#2563eb', 'width':2.5}, connectgaps=False), secondary_y=False)
        fig.add_hline(y=100, line_dash='dot', line_color='#94a3b8', secondary_y=False)
    if view in ('Nominal', 'Both'):
        fig.add_trace(go.Scatter(x=sample.index, y=sample.S_C2_per_C1,
            name='Nominal S (C2 currency / C1 currency)', line={'color':'#d97706','width':2},
            connectgaps=False), secondary_y=(view == 'Both'))
    fig.update_yaxes(title_text='Real index (sample average = 100)' if view != 'Nominal'
                     else 'Country 2 currency per Country 1 currency', secondary_y=False)
    if view == 'Both':
        fig.update_yaxes(title_text='Country 2 currency per Country 1 currency', secondary_y=True)
    fig.update_layout(height=520, hovermode='x unified', template='plotly_white',
                      legend={'orientation':'h', 'y':1.12}, margin={'t':55, 'b':30})
    st.plotly_chart(fig, use_container_width=True)
    export = sample.copy()
    export.insert(0, 'Country1', c1)
    export.insert(1, 'Country2', c2)
    for p in provenance:
        export[p['series']+'_source_URL'] = p['url']
        export[p['series']+'_retrieved_UTC'] = p['retrieved_UTC']
    export.index = export.index.strftime('%Y-%m')
    st.download_button('Download sample CSV', export.to_csv(index_label='month').encode('utf-8'),
        file_name=f'rer_{c1}_{c2}_{start}_{end}.csv', mime='text/csv')
    with st.expander('Observations and calculations'):
        st.dataframe(sample, use_container_width=True)
    with st.expander('Method and data sources'):
        st.latex(r'S_t=\frac{LCU_{2,t}/USD}{LCU_{1,t}/USD},\quad RER_t=S_t\frac{P_{1,t}}{P_{2,t}}')
        st.latex(r'RER^*=\frac{1}{N}\sum_{t\in valid}RER_t,\quad RealIndex_t=100\frac{RER_t}{RER^*}')
        st.write('RER* is a sample average, not an estimate of fair value. CPI levels have arbitrary '
                 'reference bases. Nominal S is never normalized. Monthly cross-rates are ratios of '
                 'monthly USD averages, not averages of daily bilateral cross-rates. Currency reforms '
                 'and changes in monetary regimes can create breaks; historical national currencies '
                 'are not automatically spliced into euros. No interpolation or forward-filling is used.')
        st.dataframe(pd.DataFrame(provenance), use_container_width=True)
        st.caption('Country choices come from IMF CL_COUNTRY: three-letter economies plus G163 (Euro area). '
                   'A listed economy does not guarantee data coverage. Data cached for 6 hours; metadata for 24 hours.')


if __name__ == '__main__':
    main()
