VERSION = "0.5.9"
MAILHOST = "antivirus.uwa.edu.au"
DATASTORE = "//drive.irds.uwa.edu.au/sci-ms-001"
RANDOM_PORT = 17013
STATIC_DIR = r"img|images|js|css|media|docs|tutorials|notebooks|downloads|\.well-known"

STATIC_FILES = (
    r"robots\.txt|crossdomain\.xml|favicon\.ico|browserconfig\.xml|humans\.txt"
)
# exclude these filenames/directories from static consideration
EXCLUDE = {"__pycache__"}

# Ubuntu, RHEL8
NGINX_DIRS = ("/etc/nginx/sites-enabled", "/etc/nginx/conf.d")

INDENT = "    "
NL = "\n"
