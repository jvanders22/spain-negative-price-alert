import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location(
        "build_case_study", ROOT / "scripts" / "build_case_study.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_readme_case_study_is_up_to_date(capsys):
    assert load_script().main(["--check"]) == 0, capsys.readouterr().err


def test_check_detects_a_stale_block_and_writes_nothing(tmp_path, capsys):
    script = load_script()
    (tmp_path / "data").symlink_to(ROOT / "data")
    readme = tmp_path / "README.md"
    stale = f"# x\n\n{script.START}\nold numbers\n{script.END}\n\ntail\n"
    readme.write_text(stale)
    assert script.main(["--root", str(tmp_path), "--check"]) == 1
    assert "stale" in capsys.readouterr().err and readme.read_text() == stale
    assert script.main(["--root", str(tmp_path)]) == 0
    fresh = readme.read_text()
    assert fresh.startswith(f"# x\n\n{script.START}\n") and fresh.endswith(f"{script.END}\n\ntail\n")
    assert "old numbers" not in fresh and "estimate" in fresh
    assert script.main(["--root", str(tmp_path), "--check"]) == 0
    block = (ROOT / "README.md").read_text().split(script.START)[1].split(script.END)[0]
    assert block in fresh
