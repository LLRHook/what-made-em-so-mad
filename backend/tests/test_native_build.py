"""Build-adapter error paths; all compiler calls and artifacts here are synthetic."""
from __future__ import annotations

import json
import runpy
import subprocess
from pathlib import Path

import pytest
import setuptools
from setuptools import Distribution, Extension
from setuptools.errors import CompileError


@pytest.fixture
def build_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(setuptools, "setup", lambda **kwargs: None)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "setup.py"))
    command = namespace["RustBuildExt"](Distribution())
    command.initialize_options()
    destination = tmp_path / "installed" / "_intervals.so"
    monkeypatch.setattr(command, "get_ext_fullpath", lambda _: str(destination))
    monkeypatch.delenv("CARGO_BUILD_TARGET", raising=False)
    monkeypatch.delenv("_PYTHON_HOST_PLATFORM", raising=False)
    monkeypatch.setattr(namespace["platform"], "machine", lambda: "arm64")
    monkeypatch.setattr(namespace["sys"], "platform", "darwin")
    monkeypatch.setattr(namespace["struct"], "calcsize", lambda _: 8)
    source = tmp_path / "libwmesm_intervals.dylib"
    source.write_bytes(b"synthetic-native-artifact")
    calls = []

    def compiler(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["rustc", "-vV"]:
            stdout = "rustc synthetic\nhost: aarch64-apple-darwin\n"
        elif cmd[:3] == ["rustc", "--print", "cfg"]:
            stdout = 'target_arch="aarch64"\ntarget_os="macos"\ntarget_pointer_width="64"\n'
        else:
            stdout = json.dumps({"reason": "compiler-artifact", "target": {"name": "wmesm_intervals"}, "filenames": [str(source)]})
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(namespace["subprocess"], "run", compiler)
    return command, Extension("app._native._intervals", []), destination, calls, namespace


def test_build_copies_artifact_and_forces_verified_host_target(build_adapter):
    command, extension, destination, calls, _ = build_adapter
    command.build_extension(extension)
    assert destination.read_bytes() == b"synthetic-native-artifact"
    cargo = calls[-1]
    assert cargo[cargo.index("--target") + 1] == "aarch64-apple-darwin"
    assert {"--locked", "--offline", "--release"}.issubset(cargo)


@pytest.mark.parametrize("variable", ["CARGO_BUILD_TARGET", "_PYTHON_HOST_PLATFORM"])
def test_build_rejects_cross_compilation_before_compiling(build_adapter, monkeypatch, variable):
    command, extension, _, calls, _ = build_adapter
    monkeypatch.setenv(variable, "foreign-target")
    with pytest.raises(CompileError, match="Cross-compiling"):
        command.build_extension(extension)
    assert calls == []


def test_build_rejects_mismatched_python_architecture(build_adapter, monkeypatch):
    command, extension, _, calls, namespace = build_adapter
    monkeypatch.setattr(namespace["platform"], "machine", lambda: "x86_64")
    with pytest.raises(CompileError, match="architecture differ"):
        command.build_extension(extension)
    assert not any(cmd[0] == "cargo" for cmd in calls)


@pytest.mark.parametrize("problem", ["missing_toolchain", "compiler_failure", "missing_host", "missing_artifact"])
def test_build_failures_are_explicit(build_adapter, monkeypatch, problem):
    command, extension, _, _, namespace = build_adapter
    original = namespace["subprocess"].run

    def compiler(cmd, **kwargs):
        if problem == "missing_toolchain":
            raise FileNotFoundError("synthetic missing toolchain")
        if problem == "compiler_failure":
            raise subprocess.CalledProcessError(1, cmd, "", "synthetic compiler failure")
        if problem == "missing_host":
            return subprocess.CompletedProcess(cmd, 0, "rustc synthetic", "")
        if cmd[0] == "cargo":
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return original(cmd, **kwargs)

    monkeypatch.setattr(namespace["subprocess"], "run", compiler)
    with pytest.raises(CompileError):
        command.build_extension(extension)
