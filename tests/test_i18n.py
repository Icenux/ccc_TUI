"""The Czech translation: every string the code marks with _() / N_() is
in the catalogue, with the same placeholders and markup, and switching
the language switches what's shown."""
import ast
import re
from pathlib import Path

import pytest

from ccc import i18n
from ccc.i18n import _
from ccc.i18n.cs import CS

SRC = Path(__file__).resolve().parent.parent / "src" / "ccc"
MARKERS = {"_", "N_"}


def _modules():
    return [p for p in sorted(SRC.rglob("*.py")) if "i18n" not in p.parts]


def _marked_strings() -> dict[str, str]:
    """Every literal passed to _() / N_() -> where it is."""
    found = {}
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in MARKERS
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, f"{path.relative_to(SRC)}:{node.lineno}")
    return found


@pytest.fixture
def czech():
    i18n.set_language("cs")
    yield
    i18n.set_language("en")


def test_every_marked_string_is_translated():
    missing = {text: where for text, where in _marked_strings().items() if text not in CS}
    assert not missing, "\n".join(f"{where}: {text!r}" for text, where in missing.items())


def test_catalogue_has_no_unused_entries():
    used = set(_marked_strings())
    assert sorted(set(CS) - used) == []


def test_translations_keep_placeholders_and_markup():
    def shape(text):
        return (sorted(re.findall(r"\{[a-z_]*\}", text)), sorted(re.findall(r"\[/?[a-z ]*\]", text)),
                text.count("\n") > 0)

    wrong = [text for text, translated in CS.items()
             if shape(text)[:2] != shape(translated)[:2] or not translated.strip() and text.strip()]
    assert wrong == []


def test_no_function_shadows_the_translation_function():
    """`for _ in …` or `a, _ = …` makes _ a local of that function, and
    _("…") in it then fails."""
    offenders = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports_it = any(isinstance(n, ast.ImportFrom) and n.module == "ccc.i18n" and any(a.name == "_" for a in n.names)
                         for n in tree.body)
        if not imports_it:
            continue
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_english_is_the_source_text():
    assert _("Conversations") == "Conversations"


def test_czech_switches_the_text(czech):
    assert _("Conversations") == "Konverzace"
    assert _("Not in the catalogue") == "Not in the catalogue"


def test_language_resolution(monkeypatch):
    monkeypatch.delenv("CCC_LANG", raising=False)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", "cs_CZ.UTF-8")
    assert i18n.resolve(None) == "cs"
    assert i18n.resolve("") == "cs"
    assert i18n.resolve("en") == "en"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.resolve("auto") == "en"
    assert i18n.resolve("cs") == "cs"
    monkeypatch.setenv("CCC_LANG", "en")
    assert i18n.resolve("cs") == "en"


def test_load_reads_the_setting(tmp_path, monkeypatch):
    from ccc.storage import Storage

    monkeypatch.delenv("CCC_LANG", raising=False)
    storage = Storage(str(tmp_path / "db.sqlite"))
    storage.set_setting(i18n.LANGUAGE_KEY, "cs")
    try:
        assert i18n.load(storage) == "cs"
    finally:
        i18n.set_language("en")


def test_month_and_weekday_names(czech):
    from datetime import date

    assert i18n.month_title(2026, 10) == "říjen 2026"
    assert i18n.weekday_short(date(2026, 10, 19)) == "po"


def test_retranslate_shows_a_stored_text_in_the_current_language(czech):
    english = "Delivered to the interactive session — the reply is in the transcript."
    assert i18n.retranslate(CS[english]) == CS[english]
    assert i18n.retranslate(english) == CS[english]
    assert i18n.retranslate("Claude's own reply") == "Claude's own reply"
    assert i18n.retranslate(None) is None
    i18n.set_language("en")
    assert i18n.retranslate(CS[english]) == english


def test_retranslate_covers_the_run_note_and_the_denied_approval(czech):
    # Issue #187: both texts are stored/sent in English and shown in the phone's language.
    for english in ("no changes", "Denied by the user in ccc.",
                    "not committed: auto-commit only runs in 'Bypass permissions' conversations"):
        assert i18n.retranslate(english) == CS[english] != english
