# Parabiago Oggi

Responsive local-events dashboard covering two selectable areas: Parabiago / Alto Milanese (45 km, 30 days) and Cravegna / Valle d’Ossola (30 km, 90 days).

## Product scope

- Interactive OpenStreetMap map that redraws the selected area radius
- Events grouped by day and hour
- Filters and recommended views for date, evening events, category, price, distance, confidence and search
- Event context including price, booking, age rules, transport, source and verification date
- Italian-first interface with an English toggle
- Responsive desktop and mobile layouts
- Free static hosting through GitHub Pages

## Data architecture

`config/areas.json` is the single source of area configuration and source enablement. Each refresh writes `data/areas/<area>.json`, an `data/areas.json` index and `data/source-health.json`. The collector applies per-area date and radius limits, aliases frazioni to town coordinates where needed, deduplicates records and publishes confidence labels. A source failure is retained in health data but does not block the other area.

Run locally with `python scripts/refresh_events.py`; run the deterministic parser tests with `pytest -q`. GitHub Actions refreshes twice daily and commits changed data.

## Deployment

After merging to `main`:

1. Open **Settings → Pages** in GitHub.
2. Under **Build and deployment**, select **GitHub Actions**.
3. Run the `Deploy Parabiago Oggi` workflow if it does not start automatically.

The expected public URL is:

`https://exhort-ventures.github.io/parabiago-oggi/`

## Data principles

Every published event should include an original source URL, a verification timestamp and a clear distinction between confirmed facts and inferred information. Automated aggregation cannot guarantee completeness, so users should verify the original source before travelling or paying.
