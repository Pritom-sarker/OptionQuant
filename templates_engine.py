"""Single shared Jinja2Templates instance, imported by every router."""
import os

from fastapi.templating import Jinja2Templates

# Absolute path — same reasoning as main.py's static mount: don't depend on
# the process's working directory matching the repo root.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))
