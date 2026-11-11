"""v0.4.6 regression contracts for reservation modal accessibility and layout.

The true browser assertions live in qa_tests/modal_regression_v046.py and in
independent real Flask/Chrome QA; these static checks are an early warning only.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app/static/css/site.css").read_text(encoding="utf-8")
JS = (ROOT / "app/static/js/site.js").read_text(encoding="utf-8")


def test_modal_detail_values_allow_unbroken_text_to_wrap():
    assert ".modal .definition dd" in CSS
    assert "overflow-wrap:anywhere" in CSS
    assert "grid-template-columns:75px minmax(0,1fr)" in CSS
    assert "overflow-x:hidden" not in CSS  # do not hide the overflow as a fake fix


def test_dialog_returns_focus_to_original_opener_when_closed():
    assert "function modal(title, html, opener = null)" in JS
    assert "await showBooking(id, el)" in JS
    assert "await showHistory(id, el)" in JS
    assert 'dialog.addEventListener("close"' in JS
    assert "returnFocus.focus({preventScroll: true})" in JS
    assert "!currentView(context)" in JS


def test_navigation_invalidates_old_dialog():
    assert 'document.querySelectorAll("dialog.modal[open]").forEach(dialog => dialog.close());' in JS
    assert "const ticket = ++state.renderId" in JS
