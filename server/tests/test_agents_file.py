import os
import stat

import pytest

from app.agents.agents_file import (
    AgentRegistry, AgentsFileError, AgentsFileInvalid, AgentsFileWriteError, NameRejected, parse, project_name,
)

TOKEN = "t" * 16


def test_parse_ignores_comments_and_tracks_disabled():
    entries = parse(f"# LumbungDB\n# Format: nama = token\n\nsimrs = {TOKEN}\n# lama = {TOKEN}\n")
    assert entries.active == {"simrs": TOKEN}
    assert entries.disabled == {"lama"}


@pytest.mark.parametrize("text,fragment", [
    ("bukan baris valid\n", "baris 1"),
    (f"\n.mulai-titik = {TOKEN}\n", "baris 2"),
    ("a = pendek\n", "kurang dari 16"),
    (f"a = {TOKEN}\na = {TOKEN}1\n", "ganda"),
    (f"rad.db = {TOKEN}\nrad-db = {TOKEN}1\n", "project compose"),
])
def test_invalid_lines_fail_with_line_number(text, fragment):
    with pytest.raises(AgentsFileError, match=fragment):
        parse(text)


def test_replaced_token_keeps_active_line():
    assert parse(f"# a = {TOKEN}\na = {TOKEN}9\n").active == {"a": f"{TOKEN}9"}


def test_project_name():
    assert project_name("Rad.DB_1") == "rad-db_1"


def test_add_disable_remove_cycle(tmp_path):
    path = tmp_path / "agents.conf"
    path.write_text("# kepala\n")
    path.chmod(0o600)
    registry = AgentRegistry(str(path))
    token = registry.add("baru")
    assert registry.authenticate(token) == "baru"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600   # izin 600 bertahan setelah rename

    assert registry.disable("baru") is True
    assert registry.authenticate(token) is None
    assert f"# baru = {token}" in path.read_text()         # token tidak diubah, baris tidak dihapus
    assert registry.entries().disabled == {"baru"}
    with pytest.raises(NameRejected):
        registry.add("BARU")                               # nama nonaktif tetap terpakai

    second = registry.add("kedua")
    assert registry.remove_pending("kedua") is True
    assert registry.authenticate(second) is None
    assert "kedua" not in path.read_text()
    assert "# kepala" in path.read_text()


def test_other_process_changes_are_seen(tmp_path):
    path = tmp_path / "agents.conf"
    first, second = AgentRegistry(str(path)), AgentRegistry(str(path))
    token = first.add("a")
    assert second.authenticate(token) == "a"


def test_write_failure_changes_nothing(tmp_path):
    missing = tmp_path / "tidak-ada" / "agents.conf"
    registry = AgentRegistry(str(missing))
    with pytest.raises(AgentsFileWriteError):
        registry.add("a")
    assert registry.entries().active == {}


def test_load_is_strict_but_running_reload_keeps_old_entries(tmp_path):
    path = tmp_path / "agents.conf"
    path.write_text(f"a = {TOKEN}\n")
    registry = AgentRegistry(str(path))
    registry.load()
    path.write_text("rusak\n")
    assert registry.authenticate(TOKEN) == "a"
    with pytest.raises(AgentsFileError):
        registry.load()


def test_changes_refused_when_file_invalid_at_runtime(tmp_path):
    """Temuan audit: disable() dulu melaporkan berhasil padahal token yang dicabut tetap diterima."""
    path = tmp_path / "agents.conf"
    path.write_text(f"a = {TOKEN}\n")
    registry = AgentRegistry(str(path))
    registry.load()
    broken = f"a = {TOKEN}\nbaris rusak\n"
    path.write_text(broken)
    for action in (lambda: registry.disable("a"), lambda: registry.remove_pending("a"), lambda: registry.add("b")):
        with pytest.raises(AgentsFileInvalid, match="baris 2"):
            action()
    assert path.read_text() == broken                      # file tidak disentuh
    assert isinstance(AgentsFileInvalid("x"), AgentsFileWriteError)

    path.write_text(f"a = {TOKEN}\n")                     # setelah diperbaiki, pencabutan berlaku
    assert registry.disable("a") is True
    assert registry.authenticate(TOKEN) is None


def test_missing_file_is_empty(tmp_path):
    assert AgentRegistry(str(tmp_path / "x")).load().active == {}
