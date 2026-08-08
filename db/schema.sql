-- Football Prediction Model — Database Schema
-- SQLite. Designed to hold data from multiple free sources
-- (Understat, football-data.org, StatsBomb Open Data) in one place.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Reference tables
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leagues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,              -- e.g. "Premier League"
    country         TEXT,                       -- e.g. "England"
    kind            TEXT NOT NULL DEFAULT 'club' -- 'club' or 'international'
        CHECK (kind IN ('club', 'international')),
    understat_slug  TEXT,                       -- e.g. "EPL" (Understat's league code)
    fd_org_code     TEXT,                       -- e.g. "PL" (football-data.org competition code)
    difficulty      REAL,                       -- computed league-strength score (step 3)
    UNIQUE(name, country)
);

CREATE TABLE IF NOT EXISTS seasons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id   INTEGER NOT NULL REFERENCES leagues(id),
    year_start  INTEGER NOT NULL,   -- e.g. 2025 for the 2025/26 season
    UNIQUE(league_id, year_start)
);

CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    country         TEXT,
    understat_id    TEXT,           -- Understat's internal team id (string)
    fd_org_id       TEXT,           -- football-data.org team id
    fifa_code       TEXT,           -- for national teams, ISO/FIFA code
    UNIQUE(name, country)
);

-- Maps the same real-world team to its id across different providers,
-- since names rarely match exactly ("Man United" vs "Manchester United").
CREATE TABLE IF NOT EXISTS team_aliases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL REFERENCES teams(id),
    source      TEXT NOT NULL,      -- 'understat' | 'football-data' | 'statsbomb'
    alias       TEXT NOT NULL,
    UNIQUE(source, alias)
);

-- ---------------------------------------------------------------------
-- Match data
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id       INTEGER NOT NULL REFERENCES leagues(id),
    season_id       INTEGER NOT NULL REFERENCES seasons(id),
    match_date      TEXT NOT NULL,      -- ISO 8601
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    home_goals      INTEGER,
    away_goals      INTEGER,
    home_xg         REAL,               -- NULL when source has no xG (e.g. most internationals)
    away_xg         REAL,
    home_xg_model   REAL,               -- our own per-match xG, summed from shots.model_xg (Understat matches only)
    away_xg_model   REAL,
    status          TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled', 'finished', 'postponed', 'cancelled')),
    is_neutral_venue INTEGER NOT NULL DEFAULT 0,   -- 1 for most international tournament matches
    source          TEXT NOT NULL,      -- 'understat' | 'football-data' | 'statsbomb'
    source_match_id TEXT,               -- id in the original provider, for de-duplication/debugging
    UNIQUE(source, source_match_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_date  ON matches(match_date);

-- Shot-level detail (Understat / StatsBomb only). Optional but useful
-- for validating xG and later building shot maps.
CREATE TABLE IF NOT EXISTS shots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id    INTEGER NOT NULL REFERENCES matches(id),
    team_id     INTEGER NOT NULL REFERENCES teams(id),
    player_name TEXT,
    minute      INTEGER,
    x           REAL,       -- pitch coordinate, 0-1 or 0-100 depending on source (normalize on insert)
    y           REAL,
    xg          REAL,       -- xG as reported by the source (e.g. Understat's own model)
    model_xg    REAL,       -- xG computed by our own logistic regression model (modeling/xg_model.py)
    result      TEXT,       -- 'Goal', 'SavedShot', 'MissedShots', 'BlockedShot', etc.
    situation   TEXT,       -- 'OpenPlay', 'SetPiece', 'Penalty', 'FastBreak', ...
    body_part   TEXT        -- 'Foot', 'Head', ...
);

CREATE INDEX IF NOT EXISTS idx_shots_match ON shots(match_id);

-- ---------------------------------------------------------------------
-- Derived / computed tables (populated by the modeling pipeline, not the scraper)
-- ---------------------------------------------------------------------

-- Rolling attack/defense strength per team, snapshotted after each matchday.
CREATE TABLE IF NOT EXISTS team_ratings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    as_of_date      TEXT NOT NULL,      -- ratings valid "as of" this date
    elo             REAL NOT NULL,
    attack_strength REAL,               -- relative to league/competition average
    defense_weakness REAL,
    xg_for_avg      REAL,               -- rolling weighted average xG for
    xg_against_avg  REAL,
    goals_for_avg   REAL,               -- fallback for matches/competitions with no xG
    goals_against_avg REAL,
    UNIQUE(team_id, as_of_date)
);

-- Simulation output per match: full scoreline probability matrix + summary.
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER REFERENCES matches(id),   -- NULL allowed for hypothetical/future fixtures not yet in `matches`
    generated_at    TEXT NOT NULL,
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    lambda_home     REAL NOT NULL,      -- expected goals used for simulation
    lambda_away     REAL NOT NULL,
    n_simulations   INTEGER NOT NULL,
    prob_home_win   REAL NOT NULL,
    prob_draw       REAL NOT NULL,
    prob_away_win   REAL NOT NULL,
    most_likely_score TEXT,             -- e.g. "2-1"
    confidence_flag TEXT,               -- 'xg_based' | 'goals_only_fallback'
    extra_json      TEXT                -- JSON blob of {"markets": ..., "best_picks": ...}, for webapp history detail
);

CREATE TABLE IF NOT EXISTS prediction_scorelines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   INTEGER NOT NULL REFERENCES predictions(id),
    home_goals      INTEGER NOT NULL,
    away_goals      INTEGER NOT NULL,
    probability     REAL NOT NULL
);
