import subprocess
import sys
from pathlib import Path

from celery import Celery

from webapp.config import get_settings

BASE_DIR = Path(__file__).resolve().parent.parent

_redis_url = get_settings().redis_url
celery_app = Celery("balling_jobs", broker=_redis_url, backend=_redis_url)
celery_app.conf.task_track_started = True

_OUTPUT_CAP = 8000


@celery_app.task(name="admin.run_script")
def run_script_task(script: str, args: list[str]) -> dict[str, object]:
    """Runs one of the fixed scripts in admin_registry.ADMIN_JOBS as a
    subprocess (no shell, argv list only) and captures its output. `script`
    and `args` must already be validated by the caller against the
    allowlist — this task does not re-check them."""
    proc = subprocess.run(
        [sys.executable, str(BASE_DIR / script), *args],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=3600,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout[-_OUTPUT_CAP:],
        "stderr": proc.stderr[-_OUTPUT_CAP:],
    }
