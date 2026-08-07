"""Quick post-merge sanity check — confirms Man City, Newcastle, etc. are unified."""
import sqlite3, sys

db = sys.argv[1] if len(sys.argv) > 1 else "data/football.db"
conn = sqlite3.connect(db)

print("=== Teams matching common merge targets (should be ONE row each) ===")
for name_pattern in ["Manchester City%", "Newcastle%", "Real Madrid%", "PSV%", "Liverpool%"]:
    rows = conn.execute("SELECT id, name FROM teams WHERE name LIKE ?", (name_pattern,)).fetchall()
    print(f"{name_pattern}: {rows}")

print("\n=== Man City: matches across BOTH domestic and Champions League should show under ONE team_id ===")
rows = conn.execute("""
    SELECT l.name, COUNT(*) 
    FROM matches m
    JOIN teams t ON m.home_team_id = t.id OR m.away_team_id = t.id
    JOIN leagues l ON m.league_id = l.id
    WHERE t.name = 'Manchester City'
    GROUP BY l.name
""").fetchall()
for r in rows:
    print(r)

print("\n=== Total team count (should have dropped from 527 by ~38) ===")
print(conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0])

conn.close()
