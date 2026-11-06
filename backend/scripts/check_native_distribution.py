"""Build and exercise distributions with existing tools and synthetic data only."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import zipfile

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SMOKE = r'''
import json, os, sys
from pathlib import Path
installed, data, golden = (Path(argument).resolve() for argument in sys.argv[1:])
sys.path.insert(0, str(installed))
os.environ['PATH'] = ''
import app
assert Path(app.__file__).is_relative_to(installed)
from app.core.config import Settings, reset_settings_for_tests
from app.db.database import _reset_init_cache_for_tests
from app.main import create_app
from app.services.interval_matching import _load_library, _library_path
from fastapi.testclient import TestClient
reset_settings_for_tests(Settings(data_dir=data/'data', models_dir=data/'models', _env_file=None))
_reset_init_cache_for_tests()
assert _library_path().is_relative_to(installed)
assert _load_library().wmesm_intervals_abi_version() == 1
with TestClient(create_app()) as client:
    assert client.get('/api/health').status_code == 200
    response = client.post('/api/evaluation/run', json={})
    assert response.status_code == 201, response.text
    result = response.json()
    assert result['fixture_set_path'] == 'packaged:app/evaluation/fixtures.json'
    assert result['metrics'] == json.loads(golden.read_text())
    assert client.get('/api/evaluation/runs/' + result['id']).json() == result
print('Installed wheel: native ABI, legacy metrics, API and SQLite round trip pass without Cargo on PATH.')
'''


def main():
    environment = {**os.environ, 'PIP_NO_INDEX': '1', 'RUSTUP_AUTO_INSTALL': '0'}

    def run(*arguments):
        subprocess.run([sys.executable, *arguments], cwd=BACKEND, env=environment, check=True)

    with tempfile.TemporaryDirectory(prefix='wmesm-distribution-') as temporary:
        out = Path(temporary)
        run('-m', 'pip', 'wheel', '--no-build-isolation', '--no-deps', '--wheel-dir', str(out), '.')
        wheel = next(out.glob('*.whl'))
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            libraries = [name for name in names if name.startswith('app/_native/_intervals') and name.endswith(('.so', '.pyd'))]
            assert len(libraries) == 1, names
            assert 'app/evaluation/fixtures.json' in names
            tag = wheel.stem.rsplit('-', 3)[-1]
            metadata = archive.read(next(name for name in names if name.endswith('.dist-info/WHEEL'))).decode()
            assert 'Tag: ' + '-'.join(wheel.stem.rsplit('-', 3)[-3:]) in metadata
            assert tag != 'any'
            if sys.platform == 'darwin':
                binary = out / Path(libraries[0]).name
                binary.write_bytes(archive.read(libraries[0]))
                architectures = subprocess.check_output(['lipo', '-archs', str(binary)], text=True).strip().split()
                assert architectures == [platform.machine()], architectures
                assert tag.endswith('_' + architectures[0]), (tag, architectures)
        run('-c', 'from setuptools.build_meta import build_sdist; build_sdist(' + repr(str(out)) + ')')
        sdist = next(out.glob('*.tar.gz'))
        with tarfile.open(sdist) as archive:
            names = archive.getnames()
            for required in ('native/intervals/Cargo.toml', 'native/intervals/Cargo.lock', 'native/intervals/src/lib.rs', 'setup.py'):
                assert any(name.endswith('/' + required) for name in names), required
        run('-m', 'pip', 'wheel', '--no-build-isolation', '--no-deps', '--wheel-dir', str(out / 'rebuilt'), str(sdist))
        from importlib import resources
        from tests.reference import evaluation_legacy as oracle
        fixtures = json.loads(resources.files('app.evaluation').joinpath('fixtures.json').read_text())['fixtures']
        golden = out / 'golden.json'
        golden.write_text(json.dumps(oracle._aggregate_results([oracle._score_fixture(fixture) for fixture in fixtures])))
        installed = out / 'installed'
        run('-m', 'pip', 'install', '--no-deps', '--no-compile', '--target', str(installed), str(wheel))
        run('-I', '-c', SMOKE, str(installed), str(out / 'smoke'), str(golden))


if __name__ == '__main__':
    main()
