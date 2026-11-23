import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from utils import pdf_utils


@pytest.mark.parametrize(
    "tex_text",
    [
        "\\documentclass[\n  lualatex, % engine\n  a4paper\n]{article}",
        r"\documentclass{jlreq}",
        "\n% !TeX program = LuaLaTeX\n\\documentclass{article}",
        "\\documentclass{jlreq}\n% \\bibliography{disabled}",
    ],
)
def test_lualatex_without_bibliography(tex_text: str) -> None:
    assert pdf_utils._latex_build_commands(tex_text, "draft") == [
        ["lualatex", "-interaction=nonstopmode", "draft.tex"],
    ] * 3


@pytest.mark.parametrize(
    ("upbibtex_path", "bibtex"),
    [("/usr/bin/upbibtex", "upbibtex"), (None, "bibtex")],
)
def test_lualatex_bibliography(
    monkeypatch: pytest.MonkeyPatch, upbibtex_path: str | None, bibtex: str
) -> None:
    monkeypatch.setattr(pdf_utils.shutil, "which", Mock(return_value=upbibtex_path))
    tex_text = "\\documentclass[lualatex]{jlreq}\n\\bibliography{references}"
    latex = ["lualatex", "-interaction=nonstopmode", "draft.tex"]
    assert pdf_utils._latex_build_commands(tex_text, "draft") == [
        latex,
        [bibtex, "draft"],
        latex,
        latex,
    ]


def test_pdflatex_without_engine_hint() -> None:
    tex_text = "\\documentclass{article} % lualatex\njlreq and lualatex in the body"
    latex = ["pdflatex", "-interaction=nonstopmode", "draft.tex"]
    assert pdf_utils._latex_build_commands(tex_text, "draft") == [
        latex,
        ["bibtex", "draft"],
        latex,
        latex,
    ]


@pytest.mark.parametrize(("document_class", "expected_timeout"), [
    ("jlreq", 120),
    ("article", 17),
])
def test_compile_latex_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    document_class: str,
    expected_timeout: int,
) -> None:
    (tmp_path / "draft.tex").write_text(
        f"\\documentclass{{{document_class}}}\n\\bibliography{{references}}",
        encoding="utf-8",
    )
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(pdf_utils.subprocess, "run", run)

    pdf_utils.compile_latex(tmp_path, tmp_path / "output.pdf", "draft", timeout=17)

    assert [call.kwargs["timeout"] for call in run.call_args_list] == [
        expected_timeout,
    ] * 4


def test_lualatex_reports_failed_bibliography_even_with_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "draft.tex").write_text(
        "\\documentclass{jlreq}\n\\bibliography{references}", encoding="utf-8"
    )
    monkeypatch.setattr(
        pdf_utils.shutil, "which", Mock(return_value="/usr/bin/upbibtex")
    )

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "upbibtex":
            return subprocess.CompletedProcess(command, 1, "", "Missing bibliography")
        (tmp_path / "draft.pdf").touch()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(pdf_utils.subprocess, "run", run)
    output = tmp_path / "output.pdf"
    pdf_utils.compile_latex(tmp_path, output, "draft")

    assert output.exists()
    assert "Missing bibliography" in capsys.readouterr().out
