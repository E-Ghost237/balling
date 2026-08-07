"""
Country name -> flagcdn.com country/subdivision code. Shared between app.py
(to know which local flag file to render) and download_media.py (to know
which flag files to fetch).
"""

import functools

import pycountry

# pycountry only knows sovereign ISO countries — football uses a few names
# it doesn't (UK home nations aren't separate ISO entries) or gets wrong via
# fuzzy match (its fuzzy search maps "Kosovo" to Serbia). flagcdn.com serves
# these non-ISO codes directly, so override before falling back to pycountry.
FLAG_CODE_OVERRIDES = {
    "England": "gb-eng", "Scotland": "gb-sct", "Wales": "gb-wls", "Northern Ireland": "gb-nir",
    "Republic of Ireland": "ie", "Ireland": "ie",
    "Ivory Coast": "ci", "Côte d'Ivoire": "ci",
    "DR Congo": "cd", "Congo DR": "cd",
    "Cape Verde": "cv", "Chinese Taipei": "tw", "Kosovo": "xk",
    "USA": "us", "South Korea": "kr", "North Korea": "kp", "Czech Republic": "cz",
}


@functools.lru_cache(maxsize=None)
def flag_code_for_country(country):
    if not country:
        return None
    if country in FLAG_CODE_OVERRIDES:
        return FLAG_CODE_OVERRIDES[country]
    match = pycountry.countries.get(name=country)
    if not match:
        try:
            match = pycountry.countries.search_fuzzy(country)[0]
        except LookupError:
            return None
    return match.alpha_2.lower()
