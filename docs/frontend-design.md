# Balling frontend refresh

## Scope

This refresh changes Jinja presentation, CSS, and browser-only UI interactions. Python application code, prediction models, route handlers, subscriptions, pricing, quota enforcement, authentication, payments, scrapers, and stored football data are unchanged.

- Flat off-white / forest-green / lime design system shared by customer and admin pages.
- Responsive, bilingual landing page with explicitly labelled illustrative match previews, keyboard-operable market tabs, existing accuracy data, and pricing from the existing `PLANS` registry.
- Refreshed navigation, authentication/recovery forms, fixture rows, day selectors, results, and shared account/payment/history surfaces.
- Accessible field labels, autocomplete, focus styles, skip link, mobile menu state, reduced-motion support, and focus/scroll to a newly generated result.
- The scoreline heatmap uses the same server-provided cell values with a light, normalized green tint. Only presentation changes; no probabilities or model calculations are modified.

## Styling and deployment

- `webapp/static/css/input.css`: Tailwind theme tokens and utility source.
- `webapp/static/css/app.css`: compiled utilities, committed for the existing Python/Docker deployment.
- `webapp/static/css/design.css`: readable, component-level styles and responsive rules.
- `webapp/static/js/design.js`: market tabs, navigation presentation, and result focus. No API requests or prediction calculations.

After changing Tailwind classes or theme tokens:

```sh
npm ci
npm run build:css
```

Direct changes to `design.css` and `design.js` do not need a build. Node is a development tool only; the existing Docker image already copies the static assets, so production does not require Node or a new build stage. The existing asset-version mechanism continues to cache-bust styles/scripts on restart.

Fonts (Manrope, Inter, IBM Plex Mono) are self-hosted under `webapp/static/fonts`, with their licenses. HTMX is now served locally from `webapp/static/js/vendor` at the **same 2.0.3 version** as the previous CDN script; its license is included. This avoids third-party runtime font/script requests without changing HTMX behavior.

No changes have been deployed to the hosted production site by this work.

## Verification

- All Jinja templates compile.
- `npm run build:css` succeeds.
- `tests/test_frontend.py`: 9 passing regression checks for localization, preview semantics, accessible login fields, static assets, and the unchanged fixture submission payload.
- Full test suite: **83 passed, 2 failed**. Both failures were reproduced using the original templates from the starting commit:
  - `test_register_requires_email_verification_before_reaching_simulate`
  - `test_simulate_page_lists_friendly_match_option`
  These tests expect the manual matchup picker for a normal user, while the existing templates restrict it to administrators/privileged users. That existing access rule was intentionally not changed.
- Chromium checks at 320, 390, 768, 1024, and 1440px on public pages, plus customer screens at mobile/tablet/desktop sizes.
- Tested mobile menu open/close/Escape, keyboard market tabs, password visibility, seven-day fixture alignment, HTMX league/team selection, real prediction submission, and automatic focus on results.
- Automated axe WCAG A/AA checks on public pages; manual keyboard and responsive checks supplement these, but are not a full accessibility certification.

Browser screenshots, tooling, and the disposable preview PostgreSQL database are not included in the repository.
