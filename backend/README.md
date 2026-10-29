# LocalGrowth AI backend

Minimal FastAPI service backed by SQLAlchemy and SQLite. Message jobs are durable rows processed by a local worker; outcomes are synthetic, and no messaging provider is used.

## Launch locally (PowerShell)

Run these commands from the repository root:

```powershell
Set-Location backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python scripts/init_local.py
uvicorn app.main:app --reload --no-access-log
```

`init_local.py` creates `backend/.env` with persistent phone-encryption and lookup secrets. Keep that file private and do not regenerate its keys after storing data. The SQLite database is `backend/localgrowth.db`; both it and `.env` are ignored by Git. Change `THANK_YOU_DELAY_SECONDS` in `.env` to a short value (for example, `2`) when demonstrating the seven-minute thank-you delay.

Promotional campaigns require a recorded promotional opt-in. A recent promotional job suppresses another campaign preview for that customer for `PROMOTIONAL_SUPPRESSION_HOURS` (168 hours by default); setting the promotional permission to false on a purchase records an opt-out. Thank-you permission remains independent.

Open `http://127.0.0.1:8000/docs` for the FastAPI schema. For a fictional sample customer and purchase, stop the server and run `python -m app.seed_demo` from `backend/` after local initialization.

## Test

```powershell
Set-Location backend
python -m pip install -r requirements.txt
python -m pytest -q
```
