"""Regression contracts for v0.4.5 CSS-only repair of QA-044-001."""
from pathlib import Path


CSS = (Path(__file__).resolve().parents[1] / 'app/static/css/site.css').read_text(encoding='utf-8')
JS = (Path(__file__).resolve().parents[1] / 'app/static/js/site.js').read_text(encoding='utf-8')


def test_detail_uses_min_zero_tracks_and_breaks_long_unspaced_text():
    assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in CSS
    assert '.detail-layout > .panel {\n  min-width:0' in CSS
    assert '.detail-info {\n  padding:30px;\n  min-width:0;\n  overflow-wrap:anywhere' in CSS
    assert '.detail-layout {\n    grid-template-columns:minmax(0,1fr)' in CSS


def test_detail_keeps_complete_title_description_and_seller():
    # The layout fix must not clip, ellipsize or truncate submitted text.
    assert '<h1 style="margin-top:16px">${e(product.title)}</h1>' in JS
    assert '${e(product.description)}</p>' in JS
    assert '${e(product.seller_name || "未知卖家")}' in JS
