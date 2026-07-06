"""Single shared Jinja2Templates instance, imported by every router."""
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="templates")
