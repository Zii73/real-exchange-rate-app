# Real Exchange Rate Explorer

A Python 3.12 Streamlit app using live monthly IMF CPI and exchange-rate data.

## Run

Put all three files in one directory. Install Python 3.12, open a terminal there, and run:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Windows activation: `.venv\Scripts\activate`. Open the localhost URL printed by Streamlit. This is a locally run app; no hosting account is needed. An internet connection to api.imf.org is required. These dependency ranges support Python 3.12; they are not a frozen lockfile.

## Use

Choose Country 1 and Country 2, select start and end months, then choose Real, Nominal, or Both. Plotly supports zoom, hover, pan, and image download. Both uses independent vertical axes. Download sample CSV exports every calendar month in the selected period, including missing observations.

## IMF requests

API root: https://api.imf.org/external/sdmx/3.0

- Country metadata: `/structure/dataflow/IMF.STA/CPI/+?references=all` (includes CL_COUNTRY) (SDMX structure JSON).
- CPI: `/data/dataflow/IMF.STA/CPI/+/{ISO3}.CPI._T.IX.M`.
- Exchange rate: `/data/dataflow/IMF.STA/ER/+/{ISO3}.XDC_USD.PA_RT.M`.
- Euro area code: `G163`.
- Data requests accept `application/vnd.sdmx.data+csv;version=2.0`.

The SDMX 3.0 route includes `data/dataflow`, agency, dataset and version as path segments. `+` requests the latest version. The app filters observations locally from January 1976, since server date filters may be ignored. Country choices use the IMF country codelist's three-letter economy codes plus Euro area; regional aggregates are excluded. Choices include territories and do not imply membership or series availability.

## Calculations

Let FX1 and FX2 be local-currency units per USD. Then:

```text
S(t) = FX2(t) / FX1(t)                 # Country 2 currency per Country 1 currency
RER(t) = S(t) × CPI1(t) / CPI2(t)
RER* = arithmetic mean of valid RER(t) in the selected date interval
Real_Index(t) = 100 × RER(t) / RER*
```

Changing either date recalculates the mean. The nominal chart always shows actual currency units. RER requires all four positive, finite inputs. Nominal S requires only both exchange rates and can remain available when CPI is missing. No missing values are filled. The full calendar sample is retained, chart gaps are visible, and missing RER observations are excluded from the mean. Zero valid months generates a warning and blank real calculations. Dates include the current month, which may be unavailable or incomplete.

A rising real index means real appreciation of Country 1 under the requested convention. The index average is 100 by construction; it is not a fair-value or misvaluation estimate. Raw RER* depends on the arbitrary CPI reference bases. Ratios of monthly USD averages need not equal monthly averages of daily bilateral rates. Historical currency redenominations, monetary-regime changes, and source methodology changes can create breaks; the app does not reconstruct comparable historical currency units or splice national currencies into euros. Inspect source metadata before interpreting long-run changes.

## Export and reliability

CSV includes month, country codes, CPI1/CPI2, FX1/FX2, any returned status/reference-period/unit metadata, S, RER, valid_RER, RER_star, Real_Index, source URLs and UTC retrieval times. Blank cells are missing data. `SCALE` exponents are applied to source values. Duplicate months are rejected rather than silently averaged. The app uses no mock, interpolated, or non-IMF fallback data. Request errors are displayed without producing substitute results.

Data are cached for six hours and country metadata for one day. Refresh IMF data clears the cache. Requests have timeouts and limited retries for rate limits and server errors. IMF coverage, revisions, outages and future schema changes are outside the app's control.

Official IMF API information: https://data.imf.org/en/Resource-Pages/IMF-API

## Validation note

During development the requested G163 CPI key returned no observations, while Euro area exchange rates were available. The app preserves this missing coverage and does not silently substitute HICP. The default pair is United States / China.
