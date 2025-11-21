# WikiWare Tests

This directory contains lightweight tests that exercise pure modules without hitting HTTP endpoints so they can run in CI.

## Current Coverage

- Validation helpers (`src/utils/validation.py`)
- Navigation history utilities (`src/utils/navigation_history.py`)

## Running the Tests

Install dev dependencies (e.g., `pip install -r requirements.txt` plus `pytest` if it is not already installed), then run:

```bash
pytest src/tests/
```

These tests run entirely in-process; no server needs to be running and no network calls are made.
