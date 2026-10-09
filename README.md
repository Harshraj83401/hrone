# Employee Attendance & Analytics API

Python 3.11+, FastAPI, PyMongo and MongoDB 6.0 or newer. All application code lives in `app/main.py` and implements the candidate-kit v2.0 contract.

```sh
python -m venv .venv
# Activate .venv using your shell, then:
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Set `MONGO_URI` and `MONGO_DB` in the environment. Defaults are `mongodb://localhost:27017` and `attendance_db`. An optional local `.env` never overrides real environment variables and must not be committed. Indexes are created at startup. `/health` returns 200 after MongoDB answers and required indexes are ready, otherwise 503. Interactive API documentation is at `/docs`.

Instants use strict integer epoch milliseconds; calendar dates and shifts use IST. Attendance updates enforce database uniqueness and optimistic concurrency. Analytics use MongoDB aggregations and authoritative stored metrics. Trend gaps and seven-day averages are computed inside MongoDB. The explain route uses the same query builders and indexes as the corresponding endpoints.

Validation: integration checks use a real MongoDB 7.0 server for boundary rules, concurrency, corrections, legacy records, analytics and indexed execution plans on 100,000 attendance records. No endpoint is intentionally omitted. See `REVIEW.md` and `DECISIONS.md` for findings and design rationale.
