"""Summarize what's actually in the database, per league/season."""
import sqlite3, sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/football.db"
conn = sqlite3.connect(db)

print("=== Per league/season match counts ===")
rows = conn.execute("""
    SELECT l.name, s.year_start,
           COUNT(*) as total,
           SUM(CASE WHEN m.status='finished' THEN 1 ELSE 0 END) as finished,
           SUM(CASE WHEN m.home_xg IS NULL AND m.status='finished' THEN 1 ELSE 0 END) as missing_xg
    FROM matches m
    JOIN leagues l ON m.league_id = l.id
    JOIN seasons s ON m.season_id = s.id
    GROUP BY l.name, s.year_start
    ORDER BY l.name, s.year_start
""").fetchall()
for r in rows:
    flag = "  <-- CHECK" if r[4] > 0 else ""
    print(f"{r[0]:25s} {r[1]}  total={r[2]:4d}  finished={r[3]:4d}  missing_xg={r[4]:3d}{flag}")

print("\n=== Totals ===")
print("Teams:", conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0])
print("Matches:", conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0])
print("Leagues:", conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0])
print("Seasons:", conn.execute("SELECT COUNT(*) FROM seasons").fetchone()[0])

conn.close()
