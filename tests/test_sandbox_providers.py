import asyncio
import json
import sys
from pathlib import Path
from typing import cast

import pytest

from ethos.sandbox import (
    SandboxCompletedEvent,
    SandboxEvent,
    SandboxOutputEvent,
    SandboxProvider,
    SandboxRequest,
    SandboxTerminalReason,
    SandboxUnavailableError,
    resolve_sandbox_provider,
)
from ethos.sandbox.bubblewrap import (
    BubblewrapSandboxProvider,
    _bubblewrap_invocation,
)
from ethos.sandbox.seatbelt import SeatbeltSandboxProvider, _seatbelt_profile


def request(
    tmp_path: Path,
    argv: tuple[str, ...],
    *,
    environment: dict[str, str] | None = None,
) -> SandboxRequest:
    workspace = (tmp_path / "workspace").resolve()
    temporary = (tmp_path / "temporary").resolve()
    workspace.mkdir(exist_ok=True)
    temporary.mkdir(exist_ok=True)
    return SandboxRequest(
        argv=argv,
        working_directory=workspace,
        workspace_path=workspace,
        temporary_path=temporary,
        environment=environment or {},
        timeout_seconds=3,
        max_output_bytes=4096,
    )


def test_seatbelt_profile_escapes_paths_and_denies_by_default(
    tmp_path: Path,
) -> None:
    sandbox_request = request(tmp_path, ("command",))
    profile = _seatbelt_profile(sandbox_request, ())

    assert "(deny default)" in profile
    assert "(allow network" not in profile
    assert json.dumps(str(sandbox_request.workspace_path)) in profile
    assert json.dumps(str(sandbox_request.temporary_path)) in profile
    assert json.dumps("/private/var/select/sh") in profile
    assert '(allow file-read-metadata\n  (literal "/opt")\n)' in profile


def test_bubblewrap_invocation_has_fixed_isolation_contract(
    tmp_path: Path,
) -> None:
    sandbox_request = request(
        tmp_path, ("/usr/bin/env",), environment={"ONLY": "supplied"}
    )
    invocation = _bubblewrap_invocation(
        Path("/usr/bin/bwrap"), sandbox_request, 9
    )

    for option in (
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--unshare-net",
        "--disable-userns",
        "--clearenv",
        "--new-session",
        "--die-with-parent",
        "--remount-ro",
        "--seccomp",
    ):
        assert option in invocation
    assert ("--setenv", "ONLY", "supplied") == invocation[
        invocation.index("--setenv") : invocation.index("--setenv") + 3
    ]
    assert str(Path.home()) not in invocation
    for host_path in ("/tmp", "/run"):
        assert not any(
            invocation[index : index + 3]
            in {
                ("--bind", host_path, host_path),
                ("--ro-bind", host_path, host_path),
            }
            for index in range(len(invocation) - 2)
        )
    assert "/dev/ptmx" not in invocation
    assert "--dev" not in invocation
    assert invocation[-1] == "/usr/bin/env"


def test_resolver_selects_native_provider() -> None:
    if sys.platform not in {"darwin", "linux"}:
        pytest.skip("no native provider on this platform")
    try:
        provider = asyncio.run(resolve_sandbox_provider())
    except SandboxUnavailableError as error:
        assert str(error)
        return
    assert isinstance(provider, SandboxProvider)


def test_resolver_is_the_only_platform_selection_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def available(_provider: object) -> None:
        pass

    monkeypatch.setattr(SeatbeltSandboxProvider, "check_available", available)
    monkeypatch.setattr(BubblewrapSandboxProvider, "check_available", available)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(
        asyncio.run(resolve_sandbox_provider()), SeatbeltSandboxProvider
    )
    monkeypatch.setattr(sys, "platform", "linux")
    assert isinstance(
        asyncio.run(resolve_sandbox_provider()), BubblewrapSandboxProvider
    )
    monkeypatch.setattr(sys, "platform", "unsupported")
    with pytest.raises(SandboxUnavailableError, match="unsupported"):
        asyncio.run(resolve_sandbox_provider())


@pytest.mark.skipif(
    sys.platform != "darwin" and not sys.platform.startswith("linux"),
    reason="no native provider",
)
def test_native_provider_enforces_shared_isolation_contract(
    tmp_path: Path,
) -> None:
    try:
        provider = asyncio.run(resolve_sandbox_provider())
    except SandboxUnavailableError as error:
        pytest.skip(str(error))
    outside = (tmp_path / "outside-secret").resolve()
    outside.write_text("secret", encoding="utf-8")
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    workspace_python = workspace / "python"
    workspace_python.symlink_to(Path(sys.executable).resolve())
    sandbox_request = request(
        tmp_path,
        (
            str(workspace_python),
            "-c",
            (
                "import json,os,pathlib,pty,socket;"
                "w=pathlib.Path('workspace-output');w.write_text('ok');"
                "t=pathlib.Path(os.environ['PRIVATE_TEMP'])/'temp-output';"
                "t.write_text('ok');"
                "results=[];"
                f"targets=[{str(outside)!r},'escape'];"
                "\nfor target in targets:\n"
                " try: pathlib.Path(target).read_bytes();"
                " results.append(False)\n"
                " except OSError: results.append(True)\n"
                "\nfor action in ("
                "lambda: socket.socket().connect(('127.0.0.1',9)),"
                "lambda: pty.openpty()):\n"
                " try: action(); results.append(False)\n"
                " except OSError: results.append(True)\n"
                "print(json.dumps(results))"
            ),
        ),
        environment={
            "PRIVATE_TEMP": str((tmp_path / "temporary").resolve()),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    (sandbox_request.workspace_path / "escape").symlink_to(outside)

    async def exercise() -> tuple[list[SandboxEvent], list[Path]]:
        execution = await provider.start(sandbox_request)
        profile_files = list(
            sandbox_request.temporary_path.glob("ethos-seatbelt-*.sb")
        )
        if isinstance(provider, SeatbeltSandboxProvider):
            assert len(profile_files) == 1
            assert profile_files[0].stat().st_mode & 0o777 == 0o600
        events = [event async for event in execution.events()]
        return events, profile_files

    try:
        events, profile_files = asyncio.run(exercise())
    except SandboxUnavailableError as error:
        pytest.skip(str(error))
    completed = cast(SandboxCompletedEvent, events[-1])
    output = b"".join(
        event.data for event in events if isinstance(event, SandboxOutputEvent)
    )
    assert completed.result.reason is SandboxTerminalReason.EXITED
    assert completed.result.exit_code == 0, output
    denied = json.loads(output)
    assert denied == [True, True, True, True]
    assert (
        sandbox_request.workspace_path / "workspace-output"
    ).read_text() == "ok"
    assert (sandbox_request.temporary_path / "temp-output").read_text() == "ok"
    assert all(not path.exists() for path in profile_files)

    environment_request = request(
        tmp_path,
        ("/usr/bin/env",),
        environment={"ONLY": "supplied"},
    )

    async def environment_output() -> bytes:
        execution = await provider.start(environment_request)
        events = [event async for event in execution.events()]
        return b"".join(
            event.data
            for event in events
            if isinstance(event, SandboxOutputEvent)
        )

    assert asyncio.run(environment_output()) == b"ONLY=supplied\n"
