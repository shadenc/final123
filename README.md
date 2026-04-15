# Foreign Investment Tracker

Analysis pipeline and dashboard for foreign investment flows in Saudi-listed companies (Tadawul), using quarterly financials, ownership data, and evidence screenshots.

## Quick start (local)

```bash
pip install -r requirements.txt
python start_system.py
```

Backend API only:

```bash
python src/api/evidence_api.py
```

Frontend (from `frontend/`):

```bash
npm install
npm start
```

Set `REACT_APP_API_URL` if the API is not at `http://localhost:5003`.

## Configuration

- **OpenAI (extractor):** set `OPENAI_API_KEY` in the environment or in a project-root `.env` file (never commit `.env`).
- **API CORS (production):** set `ALLOWED_ORIGINS` to your frontend origin(s).
- **Playwright:** install browsers when needed, e.g. `playwright install chromium`.

## Layout

```
src/
  api/           Flask evidence API
  scrapers/      Ownership, PDFs, quarterly data
  extractors/    PDF retained earnings extraction
  calculators/   Reinvested earnings logic
  utils/         Excel export, screenshots
frontend/        React + Material UI
data/            Inputs and generated results (large assets gitignored as configured)
output/          Screenshots and exports
```

## Deploy (summary)

Typical free setup: Python backend on **Render** (or similar) with `gunicorn` per `Procfile`, React app on **Vercel** with root directory `frontend` and `REACT_APP_API_URL` pointing at the API. Set `ALLOWED_ORIGINS` on the backend to match the frontend URL.

## Requirements

Python 3.12+, Node.js 18+ recommended, network access for scraping.
