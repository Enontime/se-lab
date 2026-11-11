"""Standalone real-Chromium regression for v0.4.6 modal layout and focus.

It runs the *production* index.html + CSS + JS through a browser with stubbed
API responses. It does not make database calls and cannot substitute for
Codex's full Flask/Chrome end-to-end acceptance suite.

Linux example:
  python qa_tests/modal_regression_v046.py --executable-path /usr/bin/chromium
Windows example (in isolated D: QA workspace):
  .venv\\Scripts\\python.exe qa_tests\\modal_regression_v046.py --channel chrome
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--channel', default=None)
    parser.add_argument('--executable-path', default=None)
    parser.add_argument('--baseline', help='Optional alternate source root containing app/')
    parser.add_argument('--out', default=None)
    parser.add_argument('--quick', action='store_true', help='one buyer/320px baseline diagnostic')
    args = parser.parse_args()
    root = Path(args.baseline).resolve() if args.baseline else Path(__file__).resolve().parents[1]
    css = (root / 'app/static/css/site.css').read_text(encoding='utf-8')
    js = (root / 'app/static/js/site.js').read_text(encoding='utf-8')
    html = (root / 'app/templates/index.html').read_text(encoding='utf-8')
    # Remove external asset tags; inject the original production CSS and JS directly.
    html = re.sub(r'<link rel="stylesheet"[^>]*>', '', html)
    html = re.sub(r'<script defer src="[^"]+"></script>', '', html)
    out = Path(args.out).resolve() if args.out else None
    if out: out.mkdir(parents=True, exist_ok=True)
    cases = []
    title = 'X' * 1000

    with sync_playwright() as pw:
        launch = {'headless': True}
        if args.executable_path: launch['executable_path'] = args.executable_path
        if args.channel: launch['channel'] = args.channel
        browser = pw.chromium.launch(**launch)
        try:
            groups = [('bookings', 'buyer')] if args.quick else [('bookings', 'buyer'), ('received', 'seller')]
            widths = (320,) if args.quick else (1440, 390, 320)
            for route_name, role in groups:
                for width in widths:
                    context = browser.new_context(viewport={'width': width, 'height': 844}, device_scale_factor=1)
                    page = context.new_page()
                    try:
                        page.goto('about:blank')
                        page.evaluate('''([roleName, name]) => {
                          const map = new Map([['campus_market_token','qa-token']]);
                          Object.defineProperty(window, 'sessionStorage', {value:{
                            getItem:k=>map.get(k)||null,setItem:(k,v)=>map.set(k,String(v)),removeItem:k=>map.delete(k)
                          }});
                          window.fetch = async (uri) => {
                            const path = new URL(uri, 'http://fixture.test').pathname;
                            let data;
                            if (path === '/api/auth/me')
                              data = {id:roleName==='buyer'?11:22,account_type:'NORMAL',username:roleName};
                            else if (['/api/reservations/mine','/api/reservations/received'].includes(path))
                              data = {items:[{id:37,product_id:4,product_title:name,
                                counterparty_name:'counterparty',status:'PENDING',
                                created_at:'2026-11-10T23:59:00',cancel_reason:null}]};
                            else if (path === '/api/reservations/37')
                              data = {id:37,product_id:4,product_title:name,
                                counterparty_name:'counterparty',status:'PENDING',expected_price_cents:100,
                                expected_version:1,created_at:'2026-11-10T23:59:00',
                                confirmed_at:null,completed_at:null,cancel_source:null,
                                cancel_reason:'Z'.repeat(1000)};
                            else return new Response(JSON.stringify({error:{message:'no fixture'}}),{status:404,
                              headers:{'Content-Type':'application/json'}});
                            return new Response(JSON.stringify(data),{status:200,
                              headers:{'Content-Type':'application/json'}});
                          };
                        }''', [role, title])
                        page.evaluate('''routeName => location.hash = '/' + routeName''', route_name)
                        page.set_content(html, wait_until='domcontentloaded')
                        page.add_style_tag(content=css)
                        page.add_script_tag(content=js)
                        opener = page.locator('[data-act="booking-detail"]').first
                        expect(opener).to_be_visible(timeout=12000)
                        expect(opener).to_be_enabled()
                        opener.focus()
                        page.keyboard.press('Enter')
                        dialog = page.locator('dialog.modal[open]')
                        expect(dialog).to_be_visible()
                        info = page.evaluate("""() => {
                            const body = document.querySelector('dialog[open] .modal-body');
                            const dd = body.querySelector('dd');
                            const items = [...body.querySelectorAll('dd')];
                            return {viewport: innerWidth, pageWidth: document.documentElement.scrollWidth,
                              bodyWidth: body.clientWidth, bodyScrollWidth: body.scrollWidth,
                              titleLength: dd.textContent.length,
                              cancelLength: items[items.length-1].textContent.length,
                              overflowWrap: getComputedStyle(dd).overflowWrap};
                        }""")
                        layout_ok = (info['bodyScrollWidth'] <= info['bodyWidth'] + 1 and
                                     info['pageWidth'] <= width + 1 and
                                     info['titleLength'] == 1000 and info['cancelLength'] == 1000)
                        cases.append({'id': f'{route_name}-{width}-wrap', 'pass': layout_ok, **info})
                        if out and width == 320:
                            page.screenshot(path=str(out / f'{role}-modal-{width}.png'))
                        for closing in ('click', 'Escape'):
                            if closing == 'click':
                                dialog.locator('[data-act="close-modal"]').click()
                            else:
                                page.keyboard.press('Escape')
                            expect(dialog).to_have_count(0)
                            try:
                                page.wait_for_function("document.activeElement?.dataset?.act === 'booking-detail'", timeout=1800)
                                focused = True
                            except Exception:
                                focused = False
                            page.keyboard.press('Enter')
                            try:
                                expect(page.locator('dialog.modal[open]')).to_be_visible(timeout=1800)
                                reopened = True
                            except Exception:
                                reopened = False
                            cases.append({'id': f'{route_name}-{width}-focus-{closing}',
                                          'pass': focused and reopened, 'focused': focused, 'reopened': reopened})
                            if closing == 'click' and reopened:
                                page.keyboard.press('Escape')
                                expect(page.locator('dialog.modal[open]')).to_have_count(0)
                        # The back/forward route may change while a dialog is open.
                        # It must close instead of leaving private details over the new route.
                        if page.locator('dialog.modal[open]').count() > 0:
                            page.keyboard.press('Escape')
                            expect(page.locator('dialog.modal[open]')).to_have_count(0)
                        opener.focus()
                        page.keyboard.press('Enter')
                        expect(page.locator('dialog.modal[open]')).to_be_visible()
                        other_route = 'received' if route_name == 'bookings' else 'bookings'
                        page.evaluate('name => location.hash = "/" + name', other_route)
                        try:
                            expect(page.locator('dialog.modal[open]')).to_have_count(0, timeout=1600)
                            expect(page.locator('[data-act="booking-detail"]').first).to_be_visible()
                            no_stale = True
                        except Exception:
                            no_stale = False
                        cases.append({'id': f'{route_name}-{width}-route-disposes-modal', 'pass': no_stale})
                    finally:
                        context.close()
        finally:
            browser.close()
    good = sum(bool(case['pass']) for case in cases)
    print(json.dumps({'cases': cases, 'passed': good, 'total': len(cases)}, ensure_ascii=False, indent=2))
    if out: (out / 'modal_results.json').write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Modal regression: {good}/{len(cases)} PASS')
    return 0 if good == len(cases) else 1


if __name__ == '__main__':
    sys.exit(run())
