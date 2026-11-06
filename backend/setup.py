"""Build the dependency-free Rust cdylib as a platform-tagged wheel artifact."""
from pathlib import Path
import json
import os
import platform
import shutil
import struct
import subprocess
import sys

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.errors import CompileError


class RustBuildExt(build_ext):
    def build_extension(self, ext):
        root = Path(__file__).resolve().parent
        # Cross builds need a target-aware wheel/ABI configuration, which this
        # backend does not supply. Fail instead of packaging a foreign library.
        if os.environ.get("CARGO_BUILD_TARGET") or os.environ.get("_PYTHON_HOST_PLATFORM"):
            raise CompileError("Cross-compiling the interval matcher is not supported; build on the target platform.")
        try:
            version = subprocess.run(["rustc", "-vV"], cwd=root, text=True, capture_output=True, check=True)
            host = next(line.removeprefix("host: ") for line in version.stdout.splitlines() if line.startswith("host: "))
            cfg = subprocess.run(["rustc", "--print", "cfg", "--target", host], cwd=root, text=True, capture_output=True, check=True)
            target = dict(line.split("=", 1) for line in cfg.stdout.splitlines() if "=" in line)
            arch = {"arm64": "aarch64", "amd64": "x86_64", "i386": "x86", "i686": "x86"}.get(platform.machine().lower(), platform.machine().lower())
            operating_system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform)
            if (target.get("target_arch") != json.dumps(arch)
                    or target.get("target_os") != json.dumps(operating_system)
                    or target.get("target_pointer_width") != json.dumps(str(struct.calcsize("P") * 8))):
                raise CompileError("Rust host and Python platform/architecture differ; use matching native toolchains.")
            result = subprocess.run(
                ["cargo", "build", "--manifest-path", str(root / "native/intervals/Cargo.toml"),
                 "--target", host, "--release", "--locked", "--offline", "--message-format=json-render-diagnostics"],
                cwd=root, text=True, capture_output=True, check=True,
            )
        except FileNotFoundError as exc:
            raise CompileError("Rust/Cargo is required to build the backend from source. See docs/setup.md.") from exc
        except StopIteration as exc:
            raise CompileError("Unable to determine the Rust compiler host platform.") from exc
        except subprocess.CalledProcessError as exc:
            raise CompileError(f"Rust interval matcher build failed:\n{exc.stderr}\n{exc.stdout}") from exc
        self.announce(result.stderr, level=2)
        artifacts = [
            Path(filename)
            for line in result.stdout.splitlines()
            for message in [json.loads(line)]
            if message.get("reason") == "compiler-artifact"
            and message["target"]["name"] == "wmesm_intervals"
            for filename in message["filenames"]
            if Path(filename).suffix in (".so", ".dylib", ".dll")
        ]
        if len(artifacts) != 1:
            raise CompileError("Cargo did not produce exactly one interval matcher shared library.")
        destination = Path(self.get_ext_fullpath(ext.name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifacts[0], destination)


setup(
    ext_modules=[Extension("app._native._intervals", sources=["native/intervals/src/lib.rs"])],
    cmdclass={"build_ext": RustBuildExt},
)
