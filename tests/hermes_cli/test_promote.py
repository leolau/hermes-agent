from argparse import Namespace

import pytest

from hermes_cli import promote


def test_promote_command_exits_nonzero_when_approval_is_denied(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def deny_promotion(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("Promotion approval was denied")

    monkeypatch.setattr(promote, "promote_artifact", deny_promotion)

    with pytest.raises(SystemExit) as exit_info:
        promote.promote_command(
            Namespace(
                artifact="config:denied-config",
                actor="test-operator",
                approve=False,
            )
        )

    assert exit_info.value.code == 1
    assert (
        capsys.readouterr().err
        == "Promotion failed: Promotion approval was denied\n"
    )
