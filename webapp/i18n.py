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
    "nav.fixtures": {"en": "Fixtures", "fr": "Matchs"},
    "landing.cta_hero_logged_in": {"en": "See today's fixtures", "fr": "Voir les matchs du jour"},
    "nav.admin": {"en": "Admin", "fr": "Admin"},
    "nav.history": {"en": "History", "fr": "Historique"},
    "nav.feedback": {"en": "Feedback", "fr": "Avis"},
    "nav.account": {"en": "Account", "fr": "Compte"},
    "nav.logout": {"en": "Log out", "fr": "Déconnexion"},
    "nav.login": {"en": "Log in", "fr": "Connexion"},
    "nav.register": {"en": "Sign up free", "fr": "Inscription gratuite"},
    "nav.lang_switch": {"en": "FR", "fr": "EN"},
    "nav.lang_switch_title": {"en": "Voir en français", "fr": "View in English"},

    # --- landing.html (public homepage) ---
    "landing.title": {"en": "Balling Predictions | Free Football Match Predictions",
                       "fr": "Balling Predictions | Pronostics football gratuits"},
    "landing.description": {
        "en": "A statistical engine that replays every match thousands of times — dynamic team "
              "ratings, xG-weighted attack/defense, large-scale simulation — to turn uncertainty "
              "into calibrated probability, published before kickoff.",
        "fr": "Un moteur statistique qui rejoue chaque match des milliers de fois — notation "
              "dynamique des équipes, attaque/défense pondérée par l'xG, simulation à grande "
              "échelle — pour transformer l'incertitude en probabilité calibrée, publiée avant "
              "le coup d'envoi.",
    },
    "landing.nav_method": {"en": "Method", "fr": "Méthode"},
    "landing.nav_coverage": {"en": "Competitions", "fr": "Compétitions"},
    "landing.nav_pricing": {"en": "Subscription", "fr": "Abonnement"},
    "landing.card_payment": {"en": "Card payment", "fr": "Carte bancaire"},
    "landing.coming_soon": {"en": "Coming soon", "fr": "Bientôt"},
    "landing.cta_free": {"en": "Create my account", "fr": "Créer mon compte"},
    "landing.cta_paid": {"en": "Subscribe", "fr": "S'abonner"},

    # --- shared ---
    "common.email": {"en": "Email", "fr": "E-mail"},
    "common.password": {"en": "Password", "fr": "Mot de passe"},
    "common.show": {"en": "Show", "fr": "Afficher"},
    "common.hide": {"en": "Hide", "fr": "Masquer"},

    # --- login.html ---
    "login.title": {"en": "Log in | Balling Predictions | Free Football Match Predictions",
                     "fr": "Connexion | Balling Predictions | Pronostics football gratuits"},
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
    "register.title": {"en": "Create a Free Account | Balling Predictions",
                        "fr": "Créer un compte gratuit | Balling Predictions"},
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

    # --- verify_email.html ---
    "verify.title": {"en": "Confirm your email | Balling Predictions",
                      "fr": "Confirmez votre e-mail | Balling Predictions"},
    "verify.heading": {"en": "Check your inbox", "fr": "Consultez votre boîte de réception"},
    "verify.subtitle_pre": {"en": "We sent a 6-digit code to", "fr": "Nous avons envoyé un code à 6 chiffres à"},
    "verify.subtitle_post": {"en": "Enter it below to confirm your email and continue.",
                              "fr": "Saisissez-le ci-dessous pour confirmer votre e-mail et continuer."},
    "verify.code_label": {"en": "Verification code", "fr": "Code de vérification"},
    "verify.confirm_submit": {"en": "Confirm email", "fr": "Confirmer l'e-mail"},
    "verify.resend_wait": {"en": "You can request a new code in {s}s.",
                            "fr": "Vous pourrez demander un nouveau code dans {s}s."},
    "verify.resend": {"en": "Resend code", "fr": "Renvoyer le code"},

    # --- forgot_password.html ---
    "forgot.title": {"en": "Forgot password | Balling Predictions",
                      "fr": "Mot de passe oublié | Balling Predictions"},
    "forgot.heading": {"en": "Reset your password", "fr": "Réinitialisez votre mot de passe"},
    "forgot.subtitle": {
        "en": "Enter your account email and we'll send you a code to reset your password.",
        "fr": "Saisissez l'e-mail de votre compte et nous vous enverrons un code pour "
              "réinitialiser votre mot de passe.",
    },
    "forgot.submit": {"en": "Send reset code", "fr": "Envoyer le code"},
    "forgot.back_to_login": {"en": "Back to log in", "fr": "Retour à la connexion"},

    # --- reset_password.html ---
    "reset.title": {"en": "Reset password | Balling Predictions",
                     "fr": "Réinitialiser le mot de passe | Balling Predictions"},
    "reset.heading": {"en": "Enter your reset code", "fr": "Saisissez votre code de réinitialisation"},
    "reset.subtitle_pre": {"en": "If an account exists for", "fr": "Si un compte existe pour"},
    "reset.subtitle_post": {"en": "we've emailed a 6-digit code.",
                             "fr": "nous vous avons envoyé un code à 6 chiffres par e-mail."},
    "reset.new_password": {"en": "New password", "fr": "Nouveau mot de passe"},
    "reset.submit": {"en": "Reset password", "fr": "Réinitialiser le mot de passe"},

    # --- simulate ---
    "simulate.title": {"en": "Simulate a Match | Balling Predictions",
                        "fr": "Simuler un match | Balling Predictions"},
    "simulate.subscribe_heading": {"en": "Subscribe to start predicting",
                                    "fr": "Abonnez-vous pour commencer à pronostiquer"},
    "simulate.subscribe_subtitle": {"en": "You need an active subscription to run match simulations.",
                                     "fr": "Un abonnement actif est nécessaire pour simuler des matchs."},
    "simulate.subscribe_now": {"en": "Subscribe now", "fr": "S'abonner maintenant"},
    "simulate.quota_exceeded": {
        "en": "You've used all {limit} distinct matchups for this cycle. It resets when your subscription renews.",
        "fr": "Vous avez utilisé les {limit} confrontations de ce cycle. Le quota se réinitialise "
              "au renouvellement de votre abonnement.",
    },
    "simulate.quota_warning": {"en": "{remaining} matchups left this cycle.",
                                "fr": "Il vous reste {remaining} confrontations ce cycle."},
    "simulate.pick_matchup": {"en": "Pick a matchup", "fr": "Choisissez un match"},
    "simulate.fixtures_heading": {"en": "This week's fixtures", "fr": "Matchs de la semaine"},
    "simulate.fixtures_subtitle": {
        "en": "Tap a match to simulate it instantly — no need to search for teams.",
        "fr": "Touchez un match pour le simuler instantanément — pas besoin de chercher les équipes.",
    },
    "simulate.fixtures_empty": {
        "en": "No fixtures loaded yet — check back soon.",
        "fr": "Aucun match chargé pour l'instant — revenez bientôt.",
    },
    "simulate.no_data_error": {
        "en": "We don't have enough match history for one of these teams yet — "
        "try again once they've played a few games this season.",
        "fr": "Nous n'avons pas encore assez d'historique pour l'une de ces équipes — "
        "réessayez une fois qu'elles auront joué quelques matchs cette saison.",
    },
    "simulate.or_pick_manually": {"en": "Or pick any matchup", "fr": "Ou choisissez un match"},
    "simulate.day_monday": {"en": "Mon", "fr": "Lun"},
    "simulate.day_tuesday": {"en": "Tue", "fr": "Mar"},
    "simulate.day_wednesday": {"en": "Wed", "fr": "Mer"},
    "simulate.day_thursday": {"en": "Thu", "fr": "Jeu"},
    "simulate.day_friday": {"en": "Fri", "fr": "Ven"},
    "simulate.day_saturday": {"en": "Sat", "fr": "Sam"},
    "simulate.day_sunday": {"en": "Sun", "fr": "Dim"},
    "simulate.league_label": {"en": "League", "fr": "Championnat"},
    "simulate.choose_league": {"en": "Choose a league…", "fr": "Choisissez un championnat…"},
    "simulate.league_hint": {
        "en": "Narrows the team lists below to clubs/countries that have played in this competition.",
        "fr": "Limite les listes d'équipes ci-dessous aux clubs/pays ayant joué dans cette compétition.",
    },
    "simulate.home_team": {"en": "Home team", "fr": "Équipe à domicile"},
    "simulate.away_team": {"en": "Away team", "fr": "Équipe à l'extérieur"},
    "simulate.select_league_first": {"en": "Select a league first", "fr": "Choisissez d'abord un championnat"},
    "simulate.choose_team": {"en": "Choose a team…", "fr": "Choisissez une équipe…"},
    "simulate.neutral_venue": {"en": "Neutral venue (no home advantage)",
                                "fr": "Terrain neutre (pas d'avantage à domicile)"},
    "simulate.submit": {"en": "Simulate match", "fr": "Simuler le match"},
    "simulate.simulating": {"en": "Simulating…", "fr": "Simulation en cours…"},
    "simulate.quota_footer": {"en": "{used} / {limit} distinct matchups used this cycle.",
                               "fr": "{used} / {limit} confrontations utilisées ce cycle."},

    # --- _result_card.html ---
    "result.elo_line": {"en": "Rating {home_elo} vs {away_elo} · {n_sims} simulations",
                         "fr": "Indice {home_elo} vs {away_elo} · {n_sims} simulations"},
    "result.win_suffix": {"en": "win", "fr": "victoire"},
    "result.draw": {"en": "Draw", "fr": "Nul"},
    "result.expected_goals": {"en": "Expected goals", "fr": "Buts attendus"},
    "result.simulations": {"en": "simulations", "fr": "simulations"},
    "result.panel_label": {"en": "Simulation result", "fr": "Résultat de la simulation"},
    "result.most_likely_score": {"en": "Most likely score", "fr": "Score le plus probable"},
    "result.saved_as": {"en": "Saved as", "fr": "Enregistré sous"},
    "result.best_picks": {"en": "Best picks", "fr": "Meilleurs pronostics"},
    "result.not_available_older": {"en": "Not available for this older prediction.",
                                    "fr": "Non disponible pour cet ancien pronostic."},
    "result.no_selection_cleared": {
        "en": "No selection cleared the 60% confidence bar for this match.",
        "fr": "Aucune sélection n'a atteint le seuil de confiance de 60% pour ce match.",
    },
    "result.scoreline_probabilities": {"en": "Scoreline probabilities", "fr": "Probabilités de score"},
    "result.rows_cols": {"en": "Rows: {home} goals · Columns: {away} goals",
                          "fr": "Lignes : buts {home} · Colonnes : buts {away}"},
    "result.btts": {"en": "Both teams to score", "fr": "Les deux équipes marquent"},
    "result.yes": {"en": "Yes", "fr": "Oui"},
    "result.no": {"en": "No", "fr": "Non"},
    "result.double_chance": {"en": "Double chance", "fr": "Double chance"},
    "result.or_draw": {"en": "or Draw", "fr": "ou Nul"},
    "result.or": {"en": "or", "fr": "ou"},
    "result.top_scorelines": {"en": "Top scorelines", "fr": "Meilleurs scores"},
    "result.total_goals": {"en": "Total goals", "fr": "Total de buts"},
    "result.line": {"en": "Line", "fr": "Ligne"},
    "result.over": {"en": "Over", "fr": "Plus de"},
    "result.under": {"en": "Under", "fr": "Moins de"},

    # --- payment.html ---
    "payment.title": {"en": "Subscribe | Balling Predictions", "fr": "Abonnement | Balling Predictions"},
    "payment.heading": {"en": "Upgrade your plan", "fr": "Améliorez votre abonnement"},
    "payment.subtitle": {
        "en": "Every account already gets the Free plan (20 matchups/month) automatically — "
              "no card, no risk. Ready for more? Pick a plan, enter your Mobile Money number "
              "below, and approve the prompt on your phone — your plan activates automatically.",
        "fr": "Chaque compte reçoit automatiquement le plan Gratuit (20 confrontations/mois) — "
              "sans carte, sans risque. Prêt pour plus ? Choisissez un plan, saisissez votre "
              "numéro Mobile Money ci-dessous et validez la demande sur votre téléphone — votre "
              "abonnement s'active automatiquement.",
    },
    "payment.selected": {"en": "Selected", "fr": "Sélectionné"},
    "payment.operator_label": {"en": "Mobile Money operator", "fr": "Opérateur Mobile Money"},
    "payment.phone_label": {"en": "Mobile Money number to charge", "fr": "Numéro Mobile Money à débiter"},
    "payment.charge_notice_pre": {"en": "You'll be charged", "fr": "Vous serez débité de"},
    "payment.pay_button": {"en": "Pay with Mobile Money", "fr": "Payer avec Mobile Money"},

    # --- payment_submitted.html ---
    "submitted.title": {"en": "Submitted | Balling Predictions", "fr": "Envoyé | Balling Predictions"},
    "submitted.heading": {"en": "Submitted for review", "fr": "Envoyé pour vérification"},
    "submitted.subtitle": {
        "en": "We'll verify your payment and activate your subscription within 24 hours. "
              "You'll be able to use Simulate as soon as it's approved.",
        "fr": "Nous vérifierons votre paiement et activerons votre abonnement sous 24 heures. "
              "Vous pourrez utiliser Simuler dès son approbation.",
    },
    "submitted.view_account": {"en": "View account status", "fr": "Voir l'état du compte"},

    # --- account.html ---
    "account.title": {"en": "Account | Balling Predictions", "fr": "Compte | Balling Predictions"},
    "account.heading": {"en": "Account", "fr": "Compte"},
    "account.logged_in_as": {"en": "Logged in as", "fr": "Connecté en tant que"},
    "account.superadmin": {"en": "Superadmin account — unlimited access, no subscription needed.",
                            "fr": "Compte super-administrateur — accès illimité, aucun abonnement requis."},
    "account.subscription": {"en": "Subscription", "fr": "Abonnement"},
    "account.free_plan": {"en": "Free plan", "fr": "Plan gratuit"},
    "account.active_plan": {"en": "{plan} — renews or expires {date}",
                             "fr": "{plan} — se renouvelle ou expire le {date}"},
    "account.active_fallback": {"en": "Active", "fr": "Actif"},
    "account.upgrade": {"en": "Upgrade", "fr": "Améliorer"},
    "account.matchups_analyzed": {"en": "Matchups analyzed this cycle", "fr": "Confrontations analysées ce cycle"},
    "account.distinct_matchups": {"en": "{used} / {limit} distinct matchups",
                                   "fr": "{used} / {limit} confrontations distinctes"},
    "account.no_subscription": {"en": "No active subscription yet.", "fr": "Aucun abonnement actif pour le moment."},
    "account.subscribe_now": {"en": "Subscribe now", "fr": "S'abonner maintenant"},

    # --- forbidden.html ---
    "forbidden.title": {"en": "Not available | Balling Predictions", "fr": "Non disponible | Balling Predictions"},
    "forbidden.heading": {"en": "Not available", "fr": "Non disponible"},
    "forbidden.needs_subscription": {
        "en": "This needs an active subscription, or you don't have access to this page.",
        "fr": "Un abonnement actif est requis, ou vous n'avez pas accès à cette page.",
    },
    "forbidden.no_access": {"en": "You don't have access to this page.",
                             "fr": "Vous n'avez pas accès à cette page."},
    "forbidden.back_to_simulate": {"en": "Back to Simulate", "fr": "Retour à Simuler"},

    # --- history_list.html / history_detail.html ---
    "history.title": {"en": "History | Balling Predictions", "fr": "Historique | Balling Predictions"},
    "history.heading": {"en": "Your prediction history", "fr": "Votre historique de pronostics"},
    "history.empty": {"en": "You haven't simulated any matches yet.",
                       "fr": "Vous n'avez encore simulé aucun match."},
    "history.simulate_a_match": {"en": "Simulate a match", "fr": "Simuler un match"},
    "history.neutral_venue": {"en": "Neutral venue", "fr": "Terrain neutre"},
    "history.most_likely_score": {"en": "most likely score", "fr": "score le plus probable"},
    "history.details_unavailable": {"en": "Details unavailable", "fr": "Détails indisponibles"},
    "history.newer": {"en": "Newer", "fr": "Plus récent"},
    "history.older": {"en": "Older", "fr": "Plus ancien"},
    "history.back_to_history": {"en": "Back to history", "fr": "Retour à l'historique"},
    "history.simulated_at": {"en": "Simulated {date}", "fr": "Simulé le {date}"},
    "history.detail_title": {"en": "{home} vs {away} | History | Balling Predictions",
                              "fr": "{home} vs {away} | Historique | Balling Predictions"},

    # --- feedback.html ---
    "feedback.title": {"en": "Feedback | Balling Predictions", "fr": "Avis | Balling Predictions"},
    "feedback.heading": {"en": "Share your feedback", "fr": "Partagez votre avis"},
    "feedback.subtitle": {
        "en": "Still early days — tell us what's working, what's confusing, or what you'd want next.",
        "fr": "C'est encore le début — dites-nous ce qui fonctionne, ce qui prête à confusion, "
              "ou ce que vous aimeriez voir ensuite.",
    },
    "feedback.thanks": {"en": "Thanks — your feedback was sent.", "fr": "Merci — votre avis a bien été envoyé."},
    "feedback.back_to_simulate": {"en": "Back to Simulate", "fr": "Retour à Simuler"},
    "feedback.rating_label": {"en": "How's it going so far? (optional)",
                               "fr": "Comment ça se passe jusqu'ici ? (facultatif)"},
    "feedback.rough": {"en": "Rough", "fr": "Difficile"},
    "feedback.great": {"en": "Great", "fr": "Excellent"},
    "feedback.message_label": {"en": "Your feedback", "fr": "Votre avis"},
    "feedback.message_placeholder": {"en": "What did you like? What would make this more useful?",
                                      "fr": "Qu'avez-vous aimé ? Qu'est-ce qui rendrait ceci plus utile ?"},
    "feedback.send": {"en": "Send feedback", "fr": "Envoyer l'avis"},

    # --- payment_pending.html / _payment_status.html ---
    "pending.title": {"en": "Payment pending | Balling Predictions",
                       "fr": "Paiement en attente | Balling Predictions"},
    "pending.check_phone": {"en": "Check your phone", "fr": "Vérifiez votre téléphone"},
    "pending.prompt_sent": {
        "en": "We sent a {operator} payment prompt to {phone} for {amount} FCFA. "
              "Enter your Mobile Money PIN on your phone to approve it.",
        "fr": "Nous avons envoyé une demande de paiement {operator} au {phone} pour "
              "{amount} FCFA. Entrez votre code PIN Mobile Money sur votre téléphone pour l'approuver.",
    },
    "pending.waiting": {"en": "Waiting for confirmation — this page updates automatically.",
                         "fr": "En attente de confirmation — cette page se met à jour automatiquement."},
    "pending.confirmed": {"en": "Payment confirmed", "fr": "Paiement confirmé"},
    "pending.active_redirect": {"en": "Your plan is now active. Redirecting…",
                                 "fr": "Votre plan est maintenant actif. Redirection…"},
    "pending.not_completed": {"en": "Payment not completed", "fr": "Paiement non abouti"},
    "pending.not_completed_detail": {
        "en": "The payment was cancelled, failed, or wasn't approved in time.",
        "fr": "Le paiement a été annulé, a échoué, ou n'a pas été approuvé à temps.",
    },
    "pending.try_again": {"en": "Try again", "fr": "Réessayer"},

    # --- footer (base.html) ---
    "footer.terms": {"en": "Terms of Service", "fr": "Conditions générales d'utilisation"},
    "footer.privacy": {"en": "Privacy Policy", "fr": "Politique de confidentialité"},
    "footer.rights": {"en": "All rights reserved.", "fr": "Tous droits réservés."},
    "footer.accuracy": {"en": "Track record", "fr": "Historique de précision"},

    # --- accuracy.html (public track record) ---
    "accuracy.title": {"en": "Track Record | Balling Predictions", "fr": "Historique de précision | Balling Predictions"},
    "accuracy.description": {
        "en": "Every prediction we've made for a real fixture, graded against the actual result — wins and misses both, nothing hidden.",
        "fr": "Chaque pronostic fait pour un vrai match, évalué par rapport au résultat réel — réussites et échecs, sans rien cacher.",
    },
    "accuracy.heading": {"en": "Track record", "fr": "Historique de précision"},
    "accuracy.subtitle": {
        "en": "Every prediction we've made for a real, scheduled fixture — graded automatically once the match finishes. Wins and misses both shown, nothing curated out.",
        "fr": "Chaque pronostic fait pour un vrai match programmé — évalué automatiquement une fois la rencontre terminée. Réussites et échecs affichés, rien n'est trié.",
    },
    "accuracy.stat_outcome_label": {"en": "Result accuracy (1X2)", "fr": "Précision du résultat (1N2)"},
    "accuracy.stat_score_label": {"en": "Exact score accuracy", "fr": "Précision du score exact"},
    "accuracy.stat_graded": {"en": "graded predictions", "fr": "pronostics évalués"},
    "accuracy.no_data": {"en": "Not enough graded predictions yet — check back soon.", "fr": "Pas encore assez de pronostics évalués — revenez bientôt."},
    "accuracy.pending": {"en": "Pending", "fr": "En attente"},
    "accuracy.correct": {"en": "Correct", "fr": "Correct"},
    "accuracy.incorrect": {"en": "Incorrect", "fr": "Incorrect"},
    "accuracy.predicted": {"en": "Predicted", "fr": "Prédit"},
    "accuracy.actual": {"en": "Actual", "fr": "Résultat"},
    "accuracy.exact_score": {"en": "Exact score", "fr": "Score exact"},
    "landing.won": {"en": "Won", "fr": "Gagnés"},
    "landing.lost": {"en": "Lost", "fr": "Perdus"},
    "landing.this_season": {"en": "this season", "fr": "cette saison"},

    # --- landing.html accuracy stats block ---
    "landing.accuracy_heading": {"en": "Our track record", "fr": "Notre historique"},
    "landing.accuracy_subtitle": {
        "en": "Every graded prediction, wins and misses both.",
        "fr": "Chaque pronostic évalué, réussites et échecs confondus.",
    },
    "landing.accuracy_view_log": {"en": "View the full log", "fr": "Voir le journal complet"},

    # --- privacy_policy.html ---
    "privacy.title": {"en": "Privacy Policy | Balling Predictions",
                       "fr": "Politique de confidentialité | Balling Predictions"},
    "privacy.description": {
        "en": "How Balling Predictions collects, uses, and protects your personal data.",
        "fr": "Comment Balling Predictions collecte, utilise et protège vos données personnelles.",
    },

    # --- terms.html ---
    "terms.title": {"en": "Terms of Service | Balling Predictions",
                     "fr": "Conditions générales d'utilisation | Balling Predictions"},
    "terms.description": {
        "en": "The terms that govern your use of Balling Predictions.",
        "fr": "Les conditions qui régissent votre utilisation de Balling Predictions.",
    },

    # --- consent notices (register.html, payment.html) ---
    "consent.register_pre": {"en": "By creating an account, you agree to our",
                              "fr": "En créant un compte, vous acceptez nos"},
    "consent.payment_pre": {"en": "By paying, you agree to our",
                             "fr": "En payant, vous acceptez nos"},
    "consent.and": {"en": "and our", "fr": "et notre"},

    # --- transactional emails (webapp/email.py, customer.py send_email calls) ---
    "email.verify.subject": {"en": "Confirm your Balling Predictions account",
                              "fr": "Confirmez votre compte Balling Predictions"},
    "email.verify.body": {
        "en": "Enter this code to verify your account. It expires in {minutes} minutes.",
        "fr": "Entrez ce code pour vérifier votre compte. Il expire dans {minutes} minutes.",
    },
    "email.reset.subject": {"en": "Reset your Balling Predictions password",
                             "fr": "Réinitialisez votre mot de passe Balling Predictions"},
    "email.reset.body": {
        "en": "Enter this code to reset your password. It expires in {minutes} minutes. "
              "If you didn't request this, you can ignore this email.",
        "fr": "Entrez ce code pour réinitialiser votre mot de passe. Il expire dans {minutes} "
              "minutes. Si vous n'êtes pas à l'origine de cette demande, vous pouvez ignorer cet e-mail.",
    },
    "email.footer": {"en": "Balling Predictions — automated match predictions",
                      "fr": "Balling Predictions — pronostics de match automatisés"},
}


# Per-plan copy (name / highlight badge / feature bullets) for the pricing
# cards on /payment. Kept separate from webapp/plans.py — that module is the
# source of truth for price/quota/duration (never translated), this is just
# its display copy. Keyed by Plan.key so a plan's numbers and its copy stay
# linked without either module importing the other.
PLAN_TRANSLATIONS: dict[str, dict[str, dict]] = {
    "free": {
        "en": {
            "name": "Free",
            "highlight": None,
            "features": (
                "20 distinct matchups / month",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "No payment required",
            ),
        },
        "fr": {
            "name": "Gratuit",
            "highlight": None,
            "features": (
                "20 confrontations distinctes / mois",
                "Tous les marchés : 1N2, double chance, BTTS, plus/moins, scores exacts",
                "Historique complet des pronostics",
                "Aucun paiement requis",
            ),
        },
    },
    "monthly": {
        "en": {
            "name": "Monthly",
            "highlight": None,
            "features": (
                "150 distinct matchups / month",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
            ),
        },
        "fr": {
            "name": "Mensuel",
            "highlight": None,
            "features": (
                "150 confrontations distinctes / mois",
                "Tous les marchés : 1N2, double chance, BTTS, plus/moins, scores exacts",
                "Historique complet des pronostics",
            ),
        },
    },
    "semiannual": {
        "en": {
            "name": "6 Months",
            "highlight": "Most popular",
            "features": (
                "1,200 distinct matchups (200/month)",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "20% cheaper than paying monthly",
            ),
        },
        "fr": {
            "name": "6 mois",
            "highlight": "Le plus populaire",
            "features": (
                "1 200 confrontations distinctes (200/mois)",
                "Tous les marchés : 1N2, double chance, BTTS, plus/moins, scores exacts",
                "Historique complet des pronostics",
                "20 % moins cher qu'un paiement mensuel",
            ),
        },
    },
    "annual": {
        "en": {
            "name": "Yearly",
            "highlight": "Best value",
            "features": (
                "3,000 distinct matchups (250/month)",
                "All markets: WDL, double chance, BTTS, over/under, scorelines",
                "Full prediction history",
                "30% cheaper than paying monthly — best value",
            ),
        },
        "fr": {
            "name": "Annuel",
            "highlight": "Meilleure offre",
            "features": (
                "3 000 confrontations distinctes (250/mois)",
                "Tous les marchés : 1N2, double chance, BTTS, plus/moins, scores exacts",
                "Historique complet des pronostics",
                "30 % moins cher qu'un paiement mensuel — meilleure offre",
            ),
        },
    },
}


def plan_t(request: Request, plan_key: str, field: str, default=None):
    """Locale-aware plan copy, falling back to `default` (typically the
    plain-English value already on the Plan dataclass) if a key or locale
    entry is missing — so a new plan added to plans.py without matching
    copy here still renders instead of breaking the page."""
    locale = getattr(request.state, "locale", DEFAULT_LOCALE)
    entry = PLAN_TRANSLATIONS.get(plan_key, {})
    copy = entry.get(locale) or entry.get(DEFAULT_LOCALE)
    if copy is None:
        return default
    return copy.get(field, default)


def t_locale(locale: str, key: str, **kwargs) -> str:
    """Locale keyed directly by string rather than by Request — for call
    sites with no request in scope, e.g. building an email body from a
    user's stored locale preference."""
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key  # missing translation — loud on purpose, easy to spot
    text = entry.get(locale) or entry.get(DEFAULT_LOCALE, key)
    return text.format(**kwargs) if kwargs else text


def t(request: Request, key: str, **kwargs) -> str:
    locale = getattr(request.state, "locale", DEFAULT_LOCALE)
    return t_locale(locale, key, **kwargs)


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
