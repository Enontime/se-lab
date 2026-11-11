"""Chromium/Chrome smoke test for QA-044-001, using the real project CSS.

This is a *component-level* layout reproduction (no Flask/database). It does NOT
replace a real-site Playwright regression against /#/product/<id>.

Windows (from independent D:\\ QA workspace):
  .venv\\Scripts\\python.exe qa_tests\\layout_stress_v045.py --channel chrome \
      --out qa_artifacts\\layout_v045

Linux (development diagnostic):
  python qa_tests/layout_stress_v045.py --executable-path /usr/bin/chromium \
      --out /mnt/data/v045_browser_smoke
"""
import argparse
import json
import sys
from html import escape
from pathlib import Path


def build_page(css, title, description, *, seller='seller1234', location='图书馆'):
    # Mirror the structure and class names emitted by site.js productDetail().
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>{css}</style></head><body><main class="main">
    <a href="#/browse" class="backlink">← 返回商品浏览</a>
    <div class="detail-layout">
      <section class="panel" style="padding:14px">
        <div class="product-image large" role="img" aria-label="商品示意图"><div class="object"></div></div>
        <p class="hint">商品示意图（当前系统不提供图片上传）</p>
      </section>
      <section class="panel detail-info">
        <div class="badge-row"><span class="pill">在售</span><span class="pill">可预约</span></div>
        <h1 id="product-title" style="margin-top:16px">{escape(title)}</h1>
        <div class="price">¥0.00</div>
        <div class="definition"><dt>商品分类</dt><dd>书籍</dd><dt>商品成色</dt><dd>全新</dd>
        <dt>交易地点</dt><dd>{escape(location)}</dd><dt>发布时间</dt><dd>2026-11-10</dd>
        <dt>商品编号</dt><dd>#25</dd><dt>卖家信息</dt><dd>{escape(seller)}（平台账号）</dd>
        <dt>交易方式</dt><dd>线下面交</dd></div>
        <h3>商品描述</h3><p id="product-description" class="sub" style="font-size:13px;white-space:pre-wrap">{escape(description)}</p>
        <div class="button-row section"><button type="button" class="button">申请预约</button></div>
      </section>
    </div></main></body></html>'''


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', default=None, help='e.g. chrome on Windows')
    parser.add_argument('--executable-path', default=None)
    parser.add_argument('--css', default=None, help='optional CSS baseline for before/after reproduction')
    parser.add_argument('--out', default=None, help='write JSON and screenshots to this directory')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    css_path = Path(args.css).resolve() if args.css else project / 'app/static/css/site.css'
    css = css_path.read_text(encoding='utf-8')
    cases = {
        'normal': ('二手教材', '无缺页，正常使用'),
        '1000_ascii': ('X' * 1000, 'D' * 1000),
        '1000_cjk': ('书' * 1000, '描' * 1000),
        'long_url': ('https://example.org/' + 'abcdef123456' * 80, 'www.' + 'z' * 995),
    }
    widths = (1440, 390, 320)
    results = []
    output = Path(args.out).resolve() if args.out else None
    if output:
        output.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as ex:
        print(f'Playwright not installed: {ex}', file=sys.stderr)
        return 2
    with sync_playwright() as p:
        launch = {'headless': True}
        if args.executable_path:
            launch['executable_path'] = args.executable_path
        elif args.channel:
            launch['channel'] = args.channel
        browser = p.chromium.launch(**launch)
        try:
            for label, (title, description) in cases.items():
                for width in widths:
                    page = browser.new_page(viewport={'width': width, 'height': 900}, device_scale_factor=1)
                    try:
                        page.set_content(build_page(css, title, description), wait_until='load')
                        info = page.evaluate('''() => {
                          const q = s => document.querySelector(s);
                          const box = q('.detail-info').getBoundingClientRect();
                          const title = q('#product-title').getBoundingClientRect();
                          const desc = q('#product-description').getBoundingClientRect();
                          const layout = q('.detail-layout');
                          return {
                            scrollWidth: document.documentElement.scrollWidth,
                            viewport: document.documentElement.clientWidth,
                            containerRight: box.right,
                            titleRight: title.right,
                            descRight: desc.right,
                            titleLen: q('#product-title').textContent.length,
                            descLen: q('#product-description').textContent.length,
                            wrapRule: getComputedStyle(q('#product-description')).overflowWrap,
                            gridColumns: getComputedStyle(layout).gridTemplateColumns,
                            titleHeight: title.height
                          };
                        }''')
                        ok = (
                            info['scrollWidth'] <= width + 1
                            and info['containerRight'] <= width + 1
                            and info['titleRight'] <= width + 1
                            and info['descRight'] <= width + 1
                            and info['titleLen'] == len(title)
                            and info['descLen'] == len(description)
                        )
                        if label != 'normal':
                            ok = ok and info['titleHeight'] > 30
                        results.append({'scenario': label, 'width': width, 'status': 'PASS' if ok else 'FAIL', **info})
                        if output and label == '1000_ascii' and width in (1440, 390):
                            page.screenshot(path=str(output / f'{label}_{width}.png'), full_page=False)
                    finally:
                        page.close()
        finally:
            browser.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if output:
        (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    fail = [x for x in results if x['status'] != 'PASS']
    print(f'Layout smoke: {len(results)-len(fail)}/{len(results)} PASS')
    return 1 if fail else 0


if __name__ == '__main__':
    raise SystemExit(run())
