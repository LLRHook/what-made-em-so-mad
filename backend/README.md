# Backend

FastAPI + SQLite. See [../docs/setup.md](../docs/setup.md) and [../docs/architecture.md](../docs/architecture.md).

```pwsh
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest -q
uvicorn app.main:app --reload --port 8000
```
