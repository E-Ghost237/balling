"""Quick sanity check on scraped Understat data. Run after the scraper."""
import sqlite3, sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/football.db"
conn = sqlite3.connect(db)

print("=== Team count ===")
print(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0])

print("\n=== Sample matches (with xG) ===")
rows = conn.execute("""
    SELECT m.match_date, ht.name, m.home_goals, m.home_xg, '-', m.away_goals, m.away_xg, at.name
    FROM matches m
    JOIN teams ht ON m.home_team_id = ht.id
    JOIN teams at ON m.away_team_id = at.id
    ORDER BY m.match_date DESC
    LIMIT 5
""").fetchall()
for r in rows:
    print(r)

print("\n=== Null xG check (should be 0 or very low for a finished EPL season) ===")
null_xg = conn.execute("SELECT COUNT(*) FROM matches WHERE home_xg IS NULL AND status='finished'").fetchone()[0]
total_finished = conn.execute("SELECT COUNT(*) FROM matches WHERE status='finished'").fetchone()[0]
print(f"{null_xg} of {total_finished} finished matches missing home_xg")

print("\n=== Goal/xG sanity range ===")
print(conn.execute("SELECT MIN(home_xg), MAX(home_xg), AVG(home_xg) FROM matches WHERE home_xg IS NOT NULL").fetchone())

conn.close()
