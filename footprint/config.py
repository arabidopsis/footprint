from __future__ import annotations

VERSION = "0.7.9"
MAILHOST = "antivirus.uwa.edu.au"
# MAILHOST="uwa-edu-au.mail.protection.outlook.com"
DATASTORE = "//drive.irds.uwa.edu.au/sci-ms-001"
RANDOM_PORT = 17013
# directories that *might* be in the static directory
STATIC_DIR = r"img|images|js|css|media|docs|tutorials|notebooks|downloads|\.well-known"

# basic files that have urls such as /robots.txt /favicon.ico etc.
STATIC_FILES = (
    r"robots\.txt|crossdomain\.xml|favicon\.ico|browserconfig\.xml|humans\.txt"
)
# exclude these filenames/directories from static consideration
EXCLUDE = {"__pycache__"}

# directory to put config files: (Ubuntu, RHEL8)
NGINX_DIRS = ("/etc/nginx/sites-enabled", "/etc/nginx/conf.d")

REPO = "git+https://github.com/arabidopsis/footprint.git"

SUDO_PASSWORD = "SUDO_PASSWORD"
ROOT_PASSWORD = "ROOT_PASSWORD"
# set to None for no color
ARG_COLOR = "yellow"
