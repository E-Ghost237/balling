# ruff: noqa: E501 — the translation catalog below has naturally long lines.
"""Two-language (English/French) support, URL-based: French pages live
under a /fr prefix (e.g. /fr/login) so Google can index and rank them as
distinct pages — that's the whole point of the URL split over a
cookie/toggle approach. Admin pages are deliberately NOT localized (the
superadmin uses the site in English; they're not part of the public,
SEO-relevant surface).

Architecture:
  - LocaleMiddleware strips a leading /fr from the request path before
    routing and records the locale on request.state — every route and
    template is written once and serves both languages.
  - t(request, key) looks up a string in TRANSLATIONS for the current
    locale. A missing key renders as the key itself (loud, easy to spot
    in review — never silently falls back to English on a typo).
  - url_for_locale(request, path) / redirect(request, path) re-prepend
    /fr to outbound links and redirects so navigating within the French
    section stays in the French section.
  - Both are registered as Jinja globals in webapp/templates.py.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

DEFAULT_LOCALE = "en"
LOCALES = ("en", "fr")

# key -> {locale: text}. Grouped roughly by where they appear; keep keys
# dotted/namespaced (page.element) so collisions are obvious at a glance.
TRANSLATIONS: dict[str, dict[str, str]] = {
    # --- nav (base.html) ---
    "nav.admin": {"en": "Admin", "fr": "Admin"},
    "nav.history": {"en": "History", "fr": "Historique"},
    "nav.feedback": {"en": "Feedback", "fr": "Avis"},
    "nav.account": {"en": "Account", "fr": "Compte"},
    "nav.logout": {"en": "Log out", "fr": "Déconnexion"},
    "nav.login": {"en": "Log in", "fr": "Connexion"},
    "nav.lang_switch": {"en": "FR", "fr": "EN"},
    "nav.lang_switch_title": {"en": "Voir en français", "fr": "View in English"},

    # --- shared ---
    "common.email": {"en": "Email", "fr": "E-mail"},
    "common.password": {"en": "Password", "fr": "Mot de passe"},
    "common.show": {"en": "Show", "fr": "Afficher"},
    "common.hide": {"en": "Hide", "fr": "Masquer"},

    # --- login.html ---
    "login.title": {"en": "Log in — Balling Predictions | Free Football Match Predictions",
                     "fr": "Connexion — Balling Predictions | Pronostics football gratuits"},
    "login.description": {
        "en": "Log in to Balling Predictions for free, data-driven football match predictions — "
              "win/draw/loss probabilities, correct scorelines, BTTS and over/under across 700+ "
              "teams in Europe's top leagues.",
        "fr": "Connectez-vous à Balling Predictions pour des pronostics football gratuits et basés "
              "sur les données — probabilités victoire/nul/défaite, score exact, BTTS et plus/moins "
              "de buts sur plus de 700 équipes des plus grands championnats européens.",
    },
    "login.heading": {"en": "Welcome back", "fr": "Content de vous revoir"},
    "login.subtitle": {"en": "Log in to run predictions on any matchup.",
                        "fr": "Connectez-vous pour générer des pronostics sur n'importe quel match."},
    "login.forgot_password": {"en": "Forgot password?", "fr": "Mot de passe oublié ?"},
    "login.submit": {"en": "Log in", "fr": "Connexion"},
    "login.new_here": {"en": "New here?", "fr": "Nouveau ici ?"},
    "login.create_account": {"en": "Create an account", "fr": "Créer un compte"},

    # --- register.html ---
    "register.title": {"en": "Create a Free Account — Balling Predictions",
                        "fr": "Créer un compte gratuit — Balling Predictions"},
    "register.description": {
        "en": "Create a free Balling Predictions account and get 20 free football match "
              "predictions every month — win/draw/loss, correct score, BTTS and over/under, "
              "no card required.",
        "fr": "Créez un compte gratuit Balling Predictions et obtenez 20 pronostics football "
              "gratuits chaque mois — victoire/nul/défaite, score exact, BTTS et plus/moins de "
              "buts, sans carte bancaire.",
    },
    "register.heading": {"en": "Create your account", "fr": "Créez votre compte"},
    "register.subtitle": {"en": "We'll email you a code to confirm it's really you before you can subscribe.",
                           "fr": "Nous vous enverrons un code par e-mail pour confirmer votre identité avant de pouvoir vous abonner."},
    "register.first_name": {"en": "First name", "fr": "Prénom"},
    "register.last_name": {"en": "Last name", "fr": "Nom"},
    "register.password_hint": {"en": "At least 8 characters.", "fr": "8 caractères minimum."},
    "register.confirm_password": {"en": "Confirm password", "fr": "Confirmer le mot de passe"},
    "register.mismatch": {"en": "Passwords do not match.", "fr": "Les mots de passe ne correspondent pas."},
    "register.submit": {"en": "Create account", "fr": "Créer le compte"},
    "register.already_have": {"en": "Already have an account?", "fr": "Vous avez déjà un compte ?"},
    "register.login_link": {"en": "Log in", "fr": "Connexion"},
}


def t(request: Request, key: str, **kwargs) -> str:
    locale = getattr(request.state, "locale", DEFAULT_LOCALE)
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key  # missing translation — loud on purpose, easy to spot
    text = entry.get(locale) or entry.get(DEFAULT_LOCALE, key)
    return text.format(**kwargs) if kwargs else text


def _strip_locale(path: str) -> str:
    if path == "/fr":
        return "/"
    if path.startswith("/fr/"):
        return path[3:]
    return path


def localize_path(path: str, locale: str) -> str:
    """The equivalent of `path` (already locale-neutral, e.g. "/login")
    under `locale` — used for outbound links/redirects and the language
    switcher. Admin paths are never localized."""
    path = _strip_locale(path)
    if locale != "fr" or path.startswith("/admin"):
        return path
    return "/fr" if path == "/" else f"/fr{path}"


def url_for_locale(request: Request, path: str) -> str:
    locale = getattr(request.state, "locale", DEFAULT_LOCALE)
    return localize_path(path, locale)


def redirect(request: Request, path: str, status_code: int = 303) -> RedirectResponse:
    return RedirectResponse(url_for_locale(request, path), status_code=status_code)


class LocaleMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.scope["path"]
        if path == "/fr" or path.startswith("/fr/"):
            request.scope["path"] = _strip_locale(path)
            request.state.locale = "fr"
        else:
            request.state.locale = DEFAULT_LOCALE
        return await call_next(request)
