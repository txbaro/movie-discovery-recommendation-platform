from fastapi.templating import Jinja2Templates

from app.core.i18n import template_i18n_context


templates = Jinja2Templates(
    directory="app/templates",
    context_processors=[template_i18n_context],
)
