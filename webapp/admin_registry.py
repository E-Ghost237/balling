"""Fixed allowlist of admin-triggerable scripts. Never accept a free-form
command from the admin UI — only these known scripts, with either no args
or a small set of server-validated args (see routes/admin.py), can be
subprocess-invoked from a request handler."""

from dataclasses import dataclass, field

UNDERSTAT_LEAGUES = ("EPL", "La_liga", "Bundesliga", "Serie_A", "Ligue_1", "RFPL")


@dataclass(frozen=True, slots=True)
class AdminJob:
    key: str
    label: str
    script: str
    args: tuple[str, ...] = field(default_factory=tuple)
    destructive_note: str | None = None


ADMIN_JOBS: dict[str, AdminJob] = {
    job.key: job
    for job in (
        AdminJob(
            "batch_scrape", "Batch scrape (Understat, all leagues)", "scrapers/batch_scrape.py"
        ),
        AdminJob(
            "footballdata_couk",
            "Football-Data.co.uk loader",
            "scrapers/footballdata_couk_loader.py",
        ),
        AdminJob(
            "footballdata_org", "Football-Data.org loader", "scrapers/footballdata_org_loader.py"
        ),
        AdminJob(
            "international_results",
            "International results loader",
            "scrapers/international_results_loader.py",
        ),
        AdminJob(
            "thesportsdb_enrich",
            "TheSportsDB enrich (logos)",
            "scrapers/thesportsdb_enrich.py",
        ),
        AdminJob(
            "rating_engine",
            "Recompute ratings (Elo + attack/defense)",
            "modeling/rating_engine.py",
        ),
        AdminJob("find_duplicate_teams", "Find duplicate teams", "db/find_duplicate_teams.py"),
        AdminJob("sanity_check", "DB sanity check", "db/sanity_check.py"),
        AdminJob("db_summary", "DB summary", "db/db_summary.py"),
        AdminJob(
            "merge_teams_dry_run",
            "Merge duplicate teams (dry run only)",
            "db/merge_teams.py",
            args=("--dry-run",),
            destructive_note=(
                "This always runs with --dry-run from the web UI and never writes to the "
                "database. A real merge is destructive and must be run by hand over SSH."
            ),
        ),
    )
}
