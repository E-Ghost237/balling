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
  - "sofascore":  api.sofascore.com's undocumented internal API (see
                  scrapers/sofascore_scraper.py) — used for leagues none of
                  the above sources cover at all.

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

# SofaScore's own numeric IDs — "tournament_id" is the unique-tournament
# (the league itself, stable across seasons); "season_ids" are that
# tournament's own season IDs, oldest first, picked by hand from
# GET /api/v1/unique-tournament/{tournament_id}/seasons (see conversation
# notes — verified directly against the live API, not guessed).
#
# Iran: 8 seasons (19/20-26/27) — the league has full history back to
# 11/12, but only the last ~7 completed seasons plus the current
# in-progress one were pulled, matching what was actually asked for.
# Iraq: only 4 seasons exist on SofaScore at all (23/24-26/27, back to
# when the league was rebranded "Iraq Stars League") — there is no deeper
# archive to pull; this is the full available history, not a subset.
SOFASCORE_LEAGUES = {
    "Persian_Gulf_Pro_League": {
        "name": "Persian Gulf Pro League",
        "country": "Iran",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 915,
        "season_ids": [24971, 34667, 39187, 44731, 52957, 65237, 79503, 99852],
    },
    "Iraq_Stars_League": {
        "name": "Iraq Stars League",
        "country": "Iraq",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 24040,
        "season_ids": [60624, 66756, 82297, 100330],
    },
    # --- UEFA club competitions: main comp + qualifying rounds, split into
    # separate league rows so a qualifier win over a modest team doesn't
    # carry the same rating weight as a group/league-phase win. SofaScore
    # nests both under the SAME tournament_id/season — there's no separate
    # "Qualification" tournament ID — so the split happens per-event via
    # each event's own event["tournament"]["name"], which SofaScore itself
    # tags distinctly (e.g. "UEFA Europa League" vs "UEFA Europa League,
    # Qualification" vs "..., Knockout stage"). See
    # scrapers/sofascore_scraper.py's round/slug fetching + name filter.
    # Last 5 seasons (22/23-26/27) — the rating engine's 180-day decay
    # half-life means only the most recent one or two actually move a
    # current rating much, but the extra seasons still give the pipeline
    # more history to work with.
    #
    # Note: no "UEFA Champions League" (main comp) entry here on purpose —
    # unlike its qualifying rounds, the main competition already has 503
    # matches in the DB from football-data.org. A first attempt fresh-
    # inserted it here anyway and produced duplicate teams/matches (team
    # names differ slightly between sources — "AS Monaco FC" vs "AS
    # Monaco" — so get_or_create_team's name+country lookup missed the
    # existing rows entirely). The main comp is enriched instead, via
    # SOFASCORE_XG_ENRICH below, which matches against the existing rows
    # rather than blindly inserting new ones.
    "UEFA_Champions_League_Qualification": {
        "name": "UEFA Champions League Qualification",
        "country": None,
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 7,
        "season_ids": [41897, 52162, 61644, 76953, 96518],
        "require_name_contains": "Qualification",
    },
    "UEFA_Europa_League": {
        "name": "UEFA Europa League",
        "country": None,
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 679,
        "season_ids": [44509, 53654, 61645, 76984, 96522],
        "exclude_name_contains": "Qualification",
    },
    "UEFA_Europa_League_Qualification": {
        "name": "UEFA Europa League Qualification",
        "country": None,
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 679,
        "season_ids": [44509, 53654, 61645, 76984, 96522],
        "require_name_contains": "Qualification",
    },
    "UEFA_Conference_League": {
        "name": "UEFA Europa Conference League",
        "country": None,
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 17015,
        "season_ids": [42224, 52327, 61648, 76960, 96529],
        "exclude_name_contains": "Qualification",
    },
    "UEFA_Conference_League_Qualification": {
        "name": "UEFA Europa Conference League Qualification",
        "country": None,
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 17015,
        "season_ids": [42224, 52327, 61648, 76960, 96529],
        "require_name_contains": "Qualification",
    },
    # --- 14-league expansion, requested to include xG. These have no
    # other source in this project at all (unlike the domestic leagues
    # above, which get xG backfilled separately via SOFASCORE_XG_ENRICH),
    # so "fetch_xg": True captures it at insert time instead — see
    # sofascore_scraper.py's insert_event()/load_league(). Last 4 seasons
    # each, per what was asked. Every tournament_id below was verified two
    # ways before being trusted: (1) checked for same-name duplicate
    # entities on SofaScore under the same country (found and rejected
    # several — e.g. two extra "Allsvenskan"/Sweden entries that turned
    # out to be bandy and handball, not football, despite matching name
    # and country), and (2) fetched a real round of fixtures and confirmed
    # the team names are actually recognizable football clubs for that
    # country, not just trusting search-result metadata.
    "Turkey_Super_Lig": {
        "name": "Süper Lig",
        "country": "Turkey",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 52,
        "season_ids": [53190, 63814, 77805, 98080],
        "fetch_xg": True,
    },
    "K_League_1": {
        "name": "K League 1",
        "country": "South Korea",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 410,
        "season_ids": [48379, 57878, 70830, 88606],
        "fetch_xg": True,
    },
    "Danish_Superliga": {
        "name": "Danish Superliga",
        "country": "Denmark",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 39,
        "season_ids": [52172, 61326, 76491, 95785],
        "fetch_xg": True,
    },
    "Scottish_Premiership": {
        "name": "Scottish Premiership",
        "country": "Scotland",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 36,
        "season_ids": [52588, 62408, 77128, 96658],
        "fetch_xg": True,
    },
    "Egyptian_Premier_League": {
        "name": "Egyptian Premier League",
        "country": "Egypt",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 808,
        "season_ids": [55005, 68147, 79317, 100148],
        "fetch_xg": True,
    },
    "MLS": {
        "name": "MLS",
        "country": "USA",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 242,
        "season_ids": [47955, 57317, 70158, 86668],
        "fetch_xg": True,
        # MLS's /rounds listing only exposes two named playoff rounds
        # (conference finals), nothing for the ~400+ match regular
        # season — MLS doesn't play a clean single-round-robin schedule
        # the way European leagues do. Confirmed directly against the
        # live API: /rounds found 2 events for a season /events/last/
        # pagination found 534 of. See sofascore_scraper.py's
        # fetch_all_events_paginated.
        "use_paginated_events": True,
    },
    "Eliteserien": {
        "name": "Eliteserien",
        "country": "Norway",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 20,
        "season_ids": [47806, 57322, 70174, 87809],
        "fetch_xg": True,
    },
    # Paraguay's top flight runs as two separate half-year tournaments per
    # calendar year (Apertura/Clausura), same clubs in both — confirmed by
    # comparing team lists, not assumed. Both feed the same "Primera
    # División"/Paraguay league row (get_or_create_league matches on
    # name+country, so this needs no special-case code) — 4 years, 8
    # season entries.
    "Paraguay_Primera_Apertura": {
        "name": "Primera División",
        "country": "Paraguay",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 11540,
        "season_ids": [47643, 57264, 69799, 87238],
        "fetch_xg": True,
    },
    "Paraguay_Primera_Clausura": {
        "name": "Primera División",
        "country": "Paraguay",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 11541,
        "season_ids": [47642, 63769, 69831, 87613],
        "fetch_xg": True,
    },
    "Romanian_Superliga": {
        "name": "Romanian Superliga",
        "country": "Romania",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 152,
        "season_ids": [52541, 62837, 77312, 97124],
        "fetch_xg": True,
    },
    "Allsvenskan": {
        "name": "Allsvenskan",
        "country": "Sweden",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 40,
        "season_ids": [47730, 57284, 69956, 87925],
        "fetch_xg": True,
    },
    "Superettan": {
        "name": "Superettan",
        "country": "Sweden",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 46,
        "season_ids": [47762, 57444, 70171, 87924],
        "fetch_xg": True,
    },
    "Swiss_Super_League": {
        "name": "Swiss Super League",
        "country": "Switzerland",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 215,
        "season_ids": [52366, 61658, 77152, 96589],
        "fetch_xg": True,
    },
    "Croatian_HNL": {
        "name": "HNL",
        "country": "Croatia",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 170,
        "season_ids": [52147, 61243, 76980, 95727],
        "fetch_xg": True,
    },
    # 2026 is J-League's transition year from calendar-year to
    # autumn-spring seasons (real, documented rule change) — both a final
    # "2026" calendar season and the new "26/27" season exist on
    # SofaScore; kept both rather than picking one, matching whatever the
    # API itself reports as the last 4 rather than second-guessing it.
    "J1_League": {
        "name": "J1 League",
        "country": "Japan",
        "kind": "club",
        "source": "sofascore",
        "tournament_id": 196,
        "season_ids": [57353, 69871, 87931, 96370],
        "fetch_xg": True,
    },
}

# xG-only enrichment targets: leagues that ALREADY have fixtures/results in
# football.db from another source (football-data.co.uk/.org) but no xG —
# see modeling/rating_engine.py's BLEND_GOALS_WEIGHT. Matched to existing
# `matches` rows by (home team, away team, date), NOT re-inserted — see
# scrapers/sofascore_xg_enrich.py. Last 5 seasons, same reasoning as
# SOFASCORE_LEAGUES above. "our_league_name" must match the
# `leagues.name` value already in the database exactly.
SOFASCORE_XG_ENRICH = {
    "La_Liga_2": {
        "our_league_name": "La Liga 2",
        "tournament_id": 54,
        "season_ids": [42410, 52563, 62048, 77558, 97280],
    },
    "Bundesliga_2": {
        "our_league_name": "Bundesliga 2",
        "tournament_id": 44,
        "season_ids": [42269, 52607, 63514, 77354, 97406],
    },
    "Ligue_2": {
        "our_league_name": "Ligue 2",
        "tournament_id": 182,
        "season_ids": [42272, 52572, 61737, 77357, 96109],
    },
    "Championship": {
        "our_league_name": "Championship",
        "tournament_id": 18,
        "season_ids": [42401, 52367, 61961, 77347, 97037],
    },
    "Serie_B": {
        "our_league_name": "Serie B",
        "tournament_id": 53,
        "season_ids": [44226, 52947, 63812, 79502, 99067],
    },
    "Belgian_Pro_League": {
        "our_league_name": "Belgian Pro League",
        "tournament_id": 38,
        "season_ids": [42404, 52383, 61459, 77040, 96616],
    },
    "Austrian_Bundesliga": {
        "our_league_name": "Austrian Bundesliga",
        "tournament_id": 45,
        "season_ids": [42386, 52524, 62629, 77382, 97043],
    },
    "Primeira_Liga": {
        "our_league_name": "Primeira Liga",
        "tournament_id": 238,
        "season_ids": [42655, 52769, 63670, 77806, 97436],
    },
    "Eredivisie": {
        "our_league_name": "Eredivisie",
        "tournament_id": 37,
        "season_ids": [42256, 52554, 61666, 77012, 96143],
    },
    "Champions_League": {
        "our_league_name": "UEFA Champions League",
        "tournament_id": 7,
        "season_ids": [41897, 52162, 61644, 76953, 96518],
        "exclude_name_contains": "Qualification",
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
