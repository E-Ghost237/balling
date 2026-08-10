import time

from fastapi.templating import Jinja2Templates

# Sourced from process start time, so it changes on every deploy (deploy.sh
# always recreates the api container = a fresh process). Used both to
# cache-bust static assets in base.html and to let the browser detect "the
# server was redeployed" via /__version__ — see base.html's poll script.
ASSET_VERSION = str(int(time.time()))

templates = Jinja2Templates(directory="webapp/templates")
templates.env.globals["asset_version"] = ASSET_VERSION
