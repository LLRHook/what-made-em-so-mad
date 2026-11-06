# Backend

FastAPI + SQLite. See [../docs/setup.md](../docs/setup.md) and [../docs/architecture.md](../docs/architecture.md).

```pwsh
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest -q
uvicorn app.main:app --reload --port 8000
```

Source and editable installs require Rust 1.85+ and Cargo on PATH. The normal
setuptools build compiles `native/intervals` with `--release --locked --offline`
and includes the shared library in a platform-tagged wheel. There are no Cargo
dependencies to download. Installing a built wheel does not require Rust.

After changing Rust source, rerun `pip install -e .` before the Python tests.
Run the native checks from this directory with:

```sh
cargo test --manifest-path native/intervals/Cargo.toml --locked --offline
cargo clippy --manifest-path native/intervals/Cargo.toml --locked --offline --all-targets -- -D warnings
cargo fmt --manifest-path native/intervals/Cargo.toml -- --check
python -m pytest tests/test_interval_matching.py tests/test_evaluation_api.py tests/test_analysis_api.py
```

With the development dependencies installed, `python scripts/check_native_distribution.py`
builds a wheel and source archive, rebuilds the source archive, checks wheel tags
against the native payload, and exercises the installed evaluation API against the
legacy oracle with a temporary SQLite database and no Cargo on PATH. The hosted
backend test workflow runs these checks on Python 3.12/3.13 across all three desktop
operating systems. It performs no deployment or model/media acquisition.

The tests use synthetic metadata and temporary SQLite databases. The frozen
Python evaluator under `tests/reference/` is the parity oracle, not a production
fallback. No media or models are needed for interval matching.
