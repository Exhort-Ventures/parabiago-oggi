# Parabiago Oggi

Responsive local-events dashboard covering a 20 km radius around Parabiago, Italy, with a rolling 30-day calendar.

## Product scope

- Interactive OpenStreetMap map with Google Maps direction links
- Events grouped by day and hour
- Filters for date, evening events, category, price and search
- Event context including price, booking, age rules, transport, source and verification date
- Italian-first interface with an English toggle
- Responsive desktop and mobile layouts
- Free static hosting through GitHub Pages

## Current status

This first pull request is a functional UI prototype. The records in `data/events.json` are explicitly marked as demonstration data and must not be treated as real events.

The next implementation phase is the source-ingestion layer: identify reliable municipal and venue sources, parse them, deduplicate records, geocode venues, enforce the 20 km boundary and replace the demonstration dataset.

## Deployment

After merging to `main`:

1. Open **Settings → Pages** in GitHub.
2. Under **Build and deployment**, select **GitHub Actions**.
3. Run the `Deploy Parabiago Oggi` workflow if it does not start automatically.

The expected public URL is:

`https://exhort-ventures.github.io/parabiago-oggi/`

## Data principles

Every published event should include an original source URL, a verification timestamp and a clear distinction between confirmed facts and inferred information. Automated aggregation cannot guarantee completeness, so users should verify the original source before travelling or paying.
