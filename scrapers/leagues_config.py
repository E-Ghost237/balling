"""
Central registry of the domestic leagues + Champions League added on top of
the 6 Understat leagues. Each entry says which free source covers it and
what code that source uses.

Sources:
  - "couk_main":  football-data.co.uk, per-season CSV file
                  URL: https://www.football-data.co.uk/mmz4281/{season_code}/{code}.csv
  - "couk_extra": football-data.co.uk, single all-seasons-in-one CSV file
                  URL: https://www.football-data.co.uk/new/{code}.csv
  - "fd_org":     football-data.org API (requires a free API key)

None of these sources provide xG — matches loaded from them get
home_xg/away_xg = NULL and should be treated as 'goals_only_fallback'
in the prediction confidence flag, same as the international-match case.
"""

DOMESTIC_LEAGUES = {
    "AUT_Bundesliga": {
        "name": "Austrian Bundesliga",
        "country": "Austria",
        "kind": "club",
        "source": "couk_extra",
        "code": "AUT",
    },
    "Eredivisie": {
        "name": "Eredivisie",
        "country": "Netherlands",
        "kind": "club",
        "source": "couk_main",
        "code": "N1",
    },
    "Belgian_Pro_League": {
        "name": "Belgian Pro League",
        "country": "Belgium",
        "kind": "club",
        "source": "couk_main",
        "code": "B1",
    },
    "Primeira_Liga": {
        "name": "Primeira Liga",
        "country": "Portugal",
        "kind": "club",
        "source": "couk_main",
        "code": "P1",
    },
    "La_Liga_2": {
        "name": "La Liga 2",
        "country": "Spain",
        "kind": "club",
        "source": "couk_main",
        "code": "SP2",
    },
    "Bundesliga_2": {
        "name": "Bundesliga 2",
        "country": "Germany",
        "kind": "club",
        "source": "couk_main",
        "code": "D2",
    },
    "Championship": {
        "name": "Championship",
        "country": "England",
        "kind": "club",
        "source": "couk_main",
        "code": "E1",
    },
    "Serie_B": {
        "name": "Serie B",
        "country": "Italy",
        "kind": "club",
        "source": "couk_main",
        "code": "I2",
    },
    "Ligue_2": {
        "name": "Ligue 2",
        "country": "France",
        "kind": "club",
        "source": "couk_main",
        "code": "F2",
    },
}

CONTINENTAL_COMPETITIONS = {
    "Champions_League": {
        "name": "UEFA Champions League",
        "country": None,
        "kind": "club",
        "source": "fd_org",
        "code": "CL",
    },
    # Europa League and Conference League intentionally omitted —
    # no free structured source found (see conversation notes).
}

# National-team competitions, loaded from the martj42/international_results
# CSV (see international_results_loader.py): a free, actively-updated,
# no-API-key dataset of ~49k international matches from 1872 to today,
# sourced from Wikipedia/rsssf/national federations.
#
# No xG is available for these (same as the couk domestic leagues) — matches
# get home_xg/away_xg = NULL and should be treated as 'goals_only_fallback'.
#
# There's no separate official FIFA-ranking data source wired in — instead,
# national teams get rated the same way club teams do, by feeding these
# matches into the existing Elo engine (rating_engine.py). That's a
# deliberate substitute for official FIFA points, which are a widely
# criticized lagging metric; a from-results Elo (as eloratings.net also
# does) is a better predictor and requires no extra scraping.
#
# "tournament_values" are the exact strings used in the source CSV's
# `tournament` column.
INTERNATIONAL_COMPETITIONS = {
    "FIFA_World_Cup": {
        "name": "FIFA World Cup",
        "country": None,
        "kind": "international",
        "tournament_values": ["FIFA World Cup"],
    },
    "FIFA_World_Cup_Qualification": {
        "name": "FIFA World Cup Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["FIFA World Cup qualification"],
    },
    "UEFA_Euro": {
        "name": "UEFA European Championship",
        "country": None,
        "kind": "international",
        "tournament_values": ["UEFA Euro"],
    },
    "UEFA_Euro_Qualification": {
        "name": "UEFA European Championship Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["UEFA Euro qualification"],
    },
    "AFCON": {
        "name": "Africa Cup of Nations",
        "country": None,
        "kind": "international",
        "tournament_values": ["African Cup of Nations"],
    },
    "AFCON_Qualification": {
        "name": "Africa Cup of Nations Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["African Cup of Nations qualification"],
    },
    "Copa_America": {
        "name": "Copa América",
        "country": None,
        "kind": "international",
        "tournament_values": ["Copa América"],
    },
    "Copa_America_Qualification": {
        "name": "Copa América Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["Copa América qualification"],
    },
    "Gold_Cup": {
        "name": "CONCACAF Gold Cup",
        "country": None,
        "kind": "international",
        "tournament_values": ["Gold Cup"],
    },
    "Gold_Cup_Qualification": {
        "name": "CONCACAF Gold Cup Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["Gold Cup qualification"],
    },
    "AFC_Asian_Cup": {
        "name": "AFC Asian Cup",
        "country": None,
        "kind": "international",
        "tournament_values": ["AFC Asian Cup"],
    },
    "AFC_Asian_Cup_Qualification": {
        "name": "AFC Asian Cup Qualification",
        "country": None,
        "kind": "international",
        "tournament_values": ["AFC Asian Cup qualification"],
    },
}
