import urllib.request
from .. import config
from . import status_check

def update_config():
    # Pull from our repo (was the upstream sch-28 fork source, which is stale).
    urllib.request.urlretrieve("https://raw.githubusercontent.com/nots0ggy/cogm_logger/main/config.ini", "config.ini")
    config.init()
    if(status_check.is_outdated()):
        print("The config is still outdated. Please update it manually.", flush=True)
    else:
        print("The config was updated successfully.", flush=True)
    