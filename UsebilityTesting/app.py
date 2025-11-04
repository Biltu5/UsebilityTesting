from flask import Flask, render_template, request, redirect, url_for, flash, send_file, g
from werkzeug.utils import secure_filename
import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time
import uuid
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from bs4.element import Tag
import tempfile
import logging
from logging.handlers import RotatingFileHandler
import math
from urllib.parse import urljoin
from urllib.request import urlopen, Request
import re
import traceback

app = Flask(__name__)
app.config['SECRET_KEY'] = 'usability-testing-secret-key'
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['REPORTS_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
app.config['ALLOWED_EXTENSIONS'] = {'html', 'htm'}

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')
file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('Usability Testing App startup')

# Request-level logging
@app.before_request
def _log_request_start():
    try:
        g._start_time = time.time()
        app.logger.info(f"--> {request.method} {request.path} from {request.remote_addr}")
    except Exception:
        pass

@app.after_request
def _log_request_end(response):
    try:
        start = getattr(g, '_start_time', None)
        dur_ms = (time.time() - start) * 1000.0 if start else 0.0
        app.logger.info(f"<-- {request.method} {request.path} status={response.status_code} duration_ms={dur_ms:.1f}")
    except Exception:
        pass
    return response

# Create necessary folders
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['REPORTS_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def safe_get(element, attribute):
    """Safely get an attribute from a BeautifulSoup Tag"""
    if isinstance(element, Tag):
        return element.get(attribute)
    return None

def run_scan(urls, screenshots_dir):
    options = Options()
    options.add_argument("--headless")
    driver = webdriver.Chrome(options=options)
    try:
        # Set a consistent viewport size to improve overlay alignment on screenshots
        driver.set_window_size(1280, 1500)
    except Exception:
        pass

    issues = []
    page_screenshots = []

    # Track cross-page properties
    title_map = {}
    nav_signatures = {}

    def parse_rgb(s):
        # Expect formats like 'rgb(r, g, b)' or 'rgba(r,g,b,a)'
        if not s:
            return (0,0,0)
        m = re.match(r"rgba?\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
        if m:
            return tuple(int(x) for x in m.groups())
        return (0,0,0)

    def relative_luminance(rgb):
        def channel(c):
            c = c/255.0
            return c/12.92 if c <= 0.03928 else ((c+0.055)/1.055) ** 2.4
        r,g,b = rgb
        return 0.2126*channel(r) + 0.7152*channel(g) + 0.0722*channel(b)

    def contrast_ratio(fg, bg):
        L1 = max(relative_luminance(fg), relative_luminance(bg))
        L2 = min(relative_luminance(fg), relative_luminance(bg))
        return (L1 + 0.05) / (L2 + 0.05)

    def capture_rule_screenshot(rects, rule_key, label_text=None):
        try:
            # If no rects but we still want a visual marker, add a small banner
            if (not rects) and label_text:
                rects = [{'left': 10, 'top': 10, 'width': 300, 'height': 30, 'label': label_text}]
            # If label_text provided, apply it to each rect
            if label_text:
                rects = [{**r, 'label': label_text} for r in rects]
            driver.execute_script("""
                (function(rects){
                  const container = document.createElement('div');
                  container.id='__scan_overlays__';
                  container.style.position='absolute';
                  container.style.left='0px';
                  container.style.top='0px';
                  container.style.width=document.documentElement.scrollWidth+'px';
                  container.style.height=document.documentElement.scrollHeight+'px';
                  container.style.pointerEvents='none';
                  container.style.zIndex='2147483647';
                  document.body.appendChild(container);
                  rects.forEach(r=>{
                    const box=document.createElement('div');
                    box.style.position='absolute';
                    box.style.left=r.left+'px';
                    box.style.top=r.top+'px';
                    box.style.width=r.width+'px';
                    box.style.height=r.height+'px';
                    box.style.border='2px solid red';
                    box.style.background='rgba(255,0,0,0.12)';
                    box.style.boxSizing='border-box';
                    box.style.pointerEvents='none';
                    container.appendChild(box);
                    if(r.label){
                      const label=document.createElement('div');
                      label.textContent=r.label;
                      label.style.position='absolute';
                      label.style.left=r.left+'px';
                      label.style.top=(r.top-18)+'px';
                      label.style.fontSize='12px';
                      label.style.background='red';
                      label.style.color='white';
                      label.style.padding='2px 4px';
                      label.style.pointerEvents='none';
                      container.appendChild(label);
                    }
                  });
                })(arguments[0]);
            """, rects)
        except Exception:
            app.logger.warning(f"[scan] Overlay injection failed for rule {rule_key}: {traceback.format_exc()}")
        fname = f"screenshot_{uuid.uuid4().hex}_{rule_key}.png"
        fpath = os.path.join(screenshots_dir, fname)
        try:
            driver.save_screenshot(fpath)
            app.logger.info(f"[scan] Rule screenshot saved: {fpath} (rule={rule_key}, overlays={len(rects) if rects else 0})")
        except Exception:
            app.logger.error(f"[scan] Failed to save rule screenshot ({rule_key}): {traceback.format_exc()}")
        try:
            driver.execute_script("var c=document.getElementById('__scan_overlays__'); if(c) c.remove();")
        except Exception:
            app.logger.warning(f"[scan] Failed to clean overlays for rule {rule_key}")
        return fname

    for i, link in enumerate(urls, start=1):
        try:
            start_t = time.time()
            app.logger.info(f"[scan] Start {link}")
            driver.get(link)
            time.sleep(1.5)

            # Page-level screenshot (no overlays)
            page_shot_name = f"screenshot_{uuid.uuid4().hex}_page.png"
            page_shot_path = os.path.join(screenshots_dir, page_shot_name)
            try:
                driver.save_screenshot(page_shot_path)
                app.logger.info(f"[scan] Page screenshot saved: {page_shot_path}")
                page_screenshots.append((link, page_shot_name))
            except Exception:
                app.logger.error(f"[scan] Failed to save page screenshot for {link}: {traceback.format_exc()}")

            soup = BeautifulSoup(driver.page_source, "html.parser")
            overlay_rects = []  # legacy aggregation (unused for page shot)

            # Gather computed styles for readability/contrast
            style_results = driver.execute_script("""
                function effectiveBackground(el){
                  let node = el;
                  while (node && node !== document.documentElement){
                    const cs = getComputedStyle(node);
                    const bg = cs.backgroundColor;
                    if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
                      return bg;
                    }
                    node = node.parentElement;
                  }
                  const bodyBg = getComputedStyle(document.body).backgroundColor;
                  if (bodyBg && bodyBg !== 'transparent' && bodyBg !== 'rgba(0, 0, 0, 0)') return bodyBg;
                  return 'rgb(255, 255, 255)';
                }
                const selectors = ['p','a','h1','h2','h3','h4','h5','h6','li','span','label'];
                const els = [];
                selectors.forEach(s=>document.querySelectorAll(s).forEach(e=>{ if(e && e.innerText && e.innerText.trim().length>0){ els.push(e); } }))
                const out = [];
                els.slice(0,80).forEach(e=>{
                    const cs = getComputedStyle(e);
                    const r = e.getBoundingClientRect();
                    out.push({
                      text: e.innerText.trim().slice(0,120),
                      color: cs.color,
                      bg: effectiveBackground(e),
                      fontSize: cs.fontSize,
                      lineHeight: cs.lineHeight,
                      left: r.left+window.scrollX,
                      top: r.top+window.scrollY,
                      width: r.width,
                      height: r.height
                    });
                });
                return out;
            """) or []

            # 1) Page Titles: unique, descriptive
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            if not title or len(title) < 5:
                shot = capture_rule_screenshot([], 'title', 'Page title')
                issues.append(["Page title not descriptive", link, "Provide a unique, descriptive <title> of at least 5 characters.", "High", shot])
            title_map[link] = title

            # 2) Headings hierarchy
            headings = soup.find_all(["h1","h2","h3","h4","h5","h6"])
            levels = [int(h.name[1]) for h in headings]
            if not any(l==1 for l in levels):
                shot = capture_rule_screenshot([], 'headings', 'Missing H1')
                issues.append(["Missing H1 heading", link, "Include a single, clear <h1> per page.", "Medium", shot])
            if levels.count(1) > 1:
                mh = driver.execute_script("return Array.from(document.querySelectorAll('h1')).map(e=>{const r=e.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Multiple H1'}}).slice(0,2);")
                shot = capture_rule_screenshot(mh or [], 'headings', 'Multiple H1')
                issues.append(["Multiple H1 headings", link, "Use only one <h1> to anchor page structure.", "Low", shot])
                
            # Detect large backward jumps in levels
            for prev, cur in zip(levels, levels[1:]):
                if cur - prev > 1:
                    hs_rect = driver.execute_script("""
                        const hs = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
                        for(let i=1;i<hs.length;i++){
                          const p = parseInt(hs[i-1].tagName.substring(1));
                          const c = parseInt(hs[i].tagName.substring(1));
                          if(c - p > 1){ const r = hs[i].getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Heading skip'} }
                        }
                        return null;
                    """)
                    shot = capture_rule_screenshot([hs_rect] if hs_rect else [], 'headings', 'Heading skip')
                    issues.append(["Heading level skips", link, "Use hierarchical headings without skipping levels (e.g., h2 then h3).", "Low", shot])
                    break

            # 3) Alternative text
            for img in soup.find_all("img"):
                alt = safe_get(img, "alt")
                src = safe_get(img, "src") or ""
                fname = os.path.basename(src)
                if not alt or len(alt.strip()) < 3 or alt.strip().lower() in {"image","photo","picture"} or alt.strip().lower() == fname.lower():
                    bad_img_rect = driver.execute_script("""
                        const imgs=document.querySelectorAll('img');
                        for(const im of imgs){ const alt = im.getAttribute('alt'); if(!alt || alt.trim().length<3){ const r=im.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Alt missing'} }
                        }
                        return null;
                    """)
                    shot = capture_rule_screenshot([bad_img_rect] if bad_img_rect else [], 'alt', 'Alt missing')
                    issues.append(["Image alt text not meaningful", link, "Provide descriptive alt text (≥3 chars, not generic or filename).", "High", shot])
                    break

            # 4) Mobile responsiveness
            viewport_meta = soup.find("meta", attrs={"name": "viewport"})
            mq_count = driver.execute_script("""
                let count=0; for (const ss of document.styleSheets){
                    try{ const rules = ss.cssRules; if(!rules) continue; for(const r of rules){ if(r.type===4) count++; } }
                    catch(e){}
                } return count;
            """)
            if not viewport_meta:
                shot = capture_rule_screenshot([], 'viewport', 'Viewport meta missing')
                issues.append(["Missing viewport meta", link, "Add <meta name='viewport' content='width=device-width, initial-scale=1.0'>.", "Medium", shot])
            if (isinstance(mq_count, int) and mq_count == 0):
                shot = capture_rule_screenshot([], 'viewport', 'No media queries')
                issues.append(["No responsive media queries", link, "Add CSS media queries to adapt layout across devices.", "Low", shot])

            # 5) Link integrity + Broken links
            anchors = soup.find_all("a")
            for a in anchors:
                href = safe_get(a, "href")
                text = (a.get_text() or "").strip()
                if not href or href in {"#", "", "javascript:void(0)"}:
                    bad_a_rect = driver.execute_script("""
                        const as=document.querySelectorAll('a');
                        for(const el of as){ const h=el.getAttribute('href'); if(!h || h==='' || h=='#' || h=='javascript:void(0)'){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Broken link'} } }
                        return null;
                    """)
                    shot = capture_rule_screenshot([bad_a_rect] if bad_a_rect else [], 'links', 'Broken link')
                    issues.append(["Empty or broken link", link, "Provide valid href and meaningful link text.", "Medium", shot])
                    break
                # Link text descriptiveness
                if text and text.lower() in {"click here","here","read more","learn more","more"}:
                    try:
                        desc_rect = driver.execute_script("""
                            const t = arguments[0].toLowerCase();
                            const el = Array.from(document.querySelectorAll('a')).find(a=>a.innerText.trim().toLowerCase()===t);
                            if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Link text'} }
                            return null;
                        """, text)
                        shot = capture_rule_screenshot([desc_rect] if desc_rect else [], 'links', 'Link text')
                        issues.append(["Link text not descriptive", link, f"Use descriptive link text instead of '{text}'.", "Low", shot])
                    except Exception:
                        pass

                try:
                    abs_url = urljoin(link, href)
                    req = Request(abs_url, headers={'User-Agent':'Mozilla/5.0'})
                    with urlopen(req, timeout=5) as resp:
                        status = getattr(resp, 'status', 200)
                    if int(status) >= 400:
                        a_rect = driver.execute_script("""
                            const h = arguments[0]; const el = document.querySelector('a[href="'+h+'"]');
                            if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Broken link'} }
                            return null;
                        """, href)
                        shot = capture_rule_screenshot([a_rect] if a_rect else [], 'links', 'Broken link')
                        issues.append(["Broken link detected", link, f"Link returns HTTP {status}: {abs_url}", "High", shot])
                        break
                except Exception:
                    a_rect = driver.execute_script("""
                        const h = arguments[0]; const el = document.querySelector('a[href="'+h+'"]');
                        if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Broken link'} }
                        return null;
                    """, href)
                    shot = capture_rule_screenshot([a_rect] if a_rect else [], 'links', 'Broken link')
                    issues.append(["Broken link detected", link, f"Failed to open: {href}", "High", shot])
                    break

            # 6) Colour contrast (WCAG 2.1) with effective background
            contrast_rects = []
            first_ratio_desc = None
            for item in style_results:
                fg = parse_rgb(item.get('color'))
                bg = parse_rgb(item.get('bg'))
                ratio = contrast_ratio(fg, bg)
                if ratio < 4.5:
                    if first_ratio_desc is None:
                        first_ratio_desc = f"Contrast {ratio:.2f}:1 below 4.5:1 for text '{item.get('text')}'."
                    contrast_rects.append({
                        'left': item.get('left',0),
                        'top': item.get('top',0),
                        'width': item.get('width',0),
                        'height': item.get('height',0),
                        'label': f"Contrast {ratio:.2f}:1"
                    })
                    if len(contrast_rects) >= 3:
                        break
            if contrast_rects:
                shot = capture_rule_screenshot(contrast_rects, 'contrast')
                issues.append(["Insufficient colour contrast", link, first_ratio_desc or "Insufficient colour contrast detected.", "High", shot])

            # 7) Keyboard navigation
            kb = driver.execute_script("""
                const nodes = document.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]');
                let tabbables = 0; nodes.forEach(n=>{ const ti = n.getAttribute('tabindex'); if(ti===null || parseInt(ti) >= 0) tabbables++; });
                const blocked = document.querySelectorAll('[tabindex="-1"]').length;
                return {tabbables, blocked};
            """)
            if kb and kb.get('tabbables',0) == 0:
                shot = capture_rule_screenshot([], 'keyboard', 'Keyboard nav')
                issues.append(["Keyboard navigation inaccessible", link, "Ensure interactive elements are reachable via keyboard (Tab).", "High", shot])
            if kb and kb.get('blocked',0) > 0:
                blocked_rects = driver.execute_script("""
                    return Array.from(document.querySelectorAll('[tabindex="-1"]').values()).slice(0,3).map(el=>{ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'tabindex -1'} });
                """)
                shot = capture_rule_screenshot(blocked_rects or [], 'keyboard', 'Tab order')
                issues.append(["Elements removed from tab order", link, "Avoid tabindex='-1' unless necessary; keep natural focus order.", "Low", shot])

            # 8) Form usability
            for form in soup.find_all('form'):
                inputs = form.find_all(['input','select','textarea'])
                for inp in inputs:
                    id_ = safe_get(inp, 'id')
                    has_label = False
                    if id_ and soup.find('label', attrs={'for': id_}):
                        has_label = True
                    if not has_label and inp.find_parent('label'):
                        has_label = True
                    if not has_label:
                        rect = driver.execute_script("""
                            const el = arguments[0]; const r = el.getBoundingClientRect();
                            return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Missing label'};
                        """, driver.find_element('css selector', inp.name if safe_get(inp,'name') else 'input,select,textarea'))
                        # Fallback: try querySelector by id
                        try:
                            if safe_get(inp,'id'):
                                rect = driver.execute_script("""
                                    const el = document.getElementById(arguments[0]); if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Missing label'} } return null;
                                """, safe_get(inp,'id'))
                        except Exception:
                            rect = None
                        shot = capture_rule_screenshot([rect] if rect else [], 'forms', 'Missing label')
                        issues.append(["Form field missing label", link, "Associate inputs with visible labels using <label for='id'>.", "Medium", shot])
                        break

            # 9) Consistent navigation (across pages)
            nav_links = [a.get_text(strip=True) for a in soup.select('nav a')][:10]
            nav_signatures[link] = "|".join(nav_links) if nav_links else ""

            # 10) Feedback mechanisms
            if not (soup.select('[role="alert"]') or soup.select('[aria-live]')):
                shot = capture_rule_screenshot([], 'feedback', 'Missing feedback')
                issues.append(["Missing user feedback hooks", link, "Provide clear feedback areas (role='alert' or aria-live) for system actions/errors.", "Low", shot])

            # 11) Readability (font size, line height)
            for item in style_results:
                try:
                    fs = float(re.sub(r'[^0-9.]+','', item.get('fontSize','12')))
                except Exception:
                    fs = 12.0
                lh_raw = item.get('lineHeight','normal')
                try:
                    lh = float(re.sub(r'[^0-9.]+','', lh_raw))
                except Exception:
                    lh = fs * 1.2
                if fs < 12:
                    r = {'left': item.get('left',0), 'top': item.get('top',0), 'width': item.get('width',0), 'height': item.get('height',0), 'label':'Small font'}
                    shot = capture_rule_screenshot([r], 'readability', 'Small font')
                    issues.append(["Small font size", link, f"Increase font size to ≥ 12px. Found {fs}px.", "Low", shot])
                    break
                if (lh/fs) if fs>0 else 1.0 < 1.2:
                    r = {'left': item.get('left',0), 'top': item.get('top',0), 'width': item.get('width',0), 'height': item.get('height',0), 'label':'Line-height'}
                    shot = capture_rule_screenshot([r], 'readability', 'Line-height')
                    issues.append(["Tight line spacing", link, "Use line-height ≈ 1.4 for comfortable reading.", "Low", shot])
                    break

            # 12) Performance
            perf = driver.execute_script("""
                const nav = performance.getEntriesByType('navigation')[0];
                if (nav) return {duration: nav.duration, load: nav.loadEventEnd - nav.startTime};
                const t = performance.timing; return {duration: t.loadEventEnd - t.navigationStart, load: t.loadEventEnd - t.navigationStart};
            """)
            dur = float(perf.get('duration', 0.0)) if perf else 0.0
            if dur > 3000:
                shot = capture_rule_screenshot([], 'performance', 'Slow page')
                issues.append(["Slow page performance", link, f"Navigation duration {dur:.0f}ms; optimize assets and requests.", "Medium", shot])

            # 13) Explicit broken links (already covered but ensure report)
            # Additional check for images with broken src
            for img in soup.find_all('img'):
                src = safe_get(img, 'src')
                if not src:
                    continue
                try:
                    abs_img = urljoin(link, src)
                    req = Request(abs_img, headers={'User-Agent':'Mozilla/5.0'})
                    with urlopen(req, timeout=5) as resp:
                        status = getattr(resp, 'status', 200)
                    if int(status) >= 400:
                        img_rect = driver.execute_script("""
                            const s = arguments[0]; const el = Array.from(document.querySelectorAll('img')).find(e=>e.getAttribute('src')===s);
                            if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Broken image'} }
                            return null;
                        """, src)
                        shot = capture_rule_screenshot([img_rect] if img_rect else [], 'images', 'Broken image')
                        issues.append(["Broken image source", link, f"Image returns HTTP {status}: {abs_img}", "Low", shot])
                        break
                except Exception:
                    img_rect = driver.execute_script("""
                        const s = arguments[0]; const el = Array.from(document.querySelectorAll('img')).find(e=>e.getAttribute('src')===s);
                        if(el){ const r=el.getBoundingClientRect(); return {left:r.left+window.scrollX, top:r.top+window.scrollY, width:r.width, height:r.height, label:'Broken image'} }
                        return null;
                    """, src)
                    shot = capture_rule_screenshot([img_rect] if img_rect else [], 'images', 'Broken image')
                    issues.append(["Broken image source", link, f"Failed to load image: {src}", "Low", shot])
                    break

            # Per-rule screenshots already captured. No combined overlay screenshot here.

        except Exception as e:
            app.logger.error(f"Error scanning {link}: {str(e)}\n{traceback.format_exc()}")
            issues.append(["Page load error", link, str(e), "High", None])
        finally:
            try:
                per_page_issues = sum(1 for it in issues if it[1] == link)
                app.logger.info(f"[scan] Done {link} in {time.time()-start_t:.1f}s issues={per_page_issues}")
            except Exception:
                pass

    # Cross-page checks: unique titles & consistent navigation
    if len(title_map) > 1:
        titles = list(title_map.values())
        dup_titles = {t for t in titles if t and titles.count(t) > 1}
        for page, t in title_map.items():
            if t in dup_titles:
                issues.append(["Non-unique page title", page, "Each page should have a unique, descriptive title.", "Medium", None])

    if len(nav_signatures) > 1:
        sigs = list(nav_signatures.values())
        majority = max(set(sigs), key=sigs.count) if sigs else ""
        for page, sig in nav_signatures.items():
            if sig and majority and sig != majority:
                issues.append(["Inconsistent navigation", page, "Use a uniform navigation structure across pages.", "Low", None])

    driver.quit()
    return issues, page_screenshots

def generate_pdf_report(issues, page_screenshots, output_path):
    app.logger.info(f"[pdf] Build start: issues={len(issues)} screenshots={len(page_screenshots)} -> {output_path}")
    doc = SimpleDocTemplate(output_path, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = [Paragraph("Usability Testing Report", styles["Title"]), Spacer(1, 20)]

    # Screenshots
    elements.append(Paragraph("Screenshots of Tested Pages:", styles["Heading2"]))
    for link, screenshot in page_screenshots:
        elements.append(Paragraph(f"Page: {link}", styles["Normal"]))
        screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'screenshots', screenshot)
        if os.path.exists(screenshot_path):
            elements.append(Image(screenshot_path, width=300, height=200))
        else:
            app.logger.warning(f"[pdf] Missing screenshot file: {screenshot_path}")
        elements.append(Spacer(1, 10))

    # Issues
    elements.append(Spacer(1, 20))
    if issues:
        elements.append(Paragraph("Detected Issues:", styles["Heading2"]))
        
        # Create a custom style for table cells
        cell_style = ParagraphStyle(
            'CellStyle',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,  # Line spacing
            spaceAfter=6
        )
        
        # Prepare data with Paragraph objects for proper text wrapping and screenshot thumbnail
        data = [["Issue", "Page", "Description", "Severity", "Screenshot"]]
        for issue in issues:
            issue_name, link, desc, severity, shot_name = issue
            # Resolve screenshot path under static/screenshots
            thumb = Paragraph("N/A", cell_style)
            try:
                if shot_name:
                    shot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'screenshots', shot_name)
                    if os.path.exists(shot_path):
                        thumb = Image(shot_path, width=150, height=100)
                    else:
                        app.logger.warning(f"[pdf] Missing issue screenshot file: {shot_path}")
            except Exception:
                app.logger.warning(f"[pdf] Failed to include issue screenshot: {traceback.format_exc()}")
            row = [
                Paragraph(issue_name, cell_style),
                Paragraph(link, cell_style),
                Paragraph(desc, cell_style),
                Paragraph(severity, cell_style),
                thumb
            ]
            data.append(row)

        # Create table with more space
        available_width = doc.width
        col_widths = [available_width * 0.2, available_width * 0.22, available_width * 0.31, available_width * 0.12, available_width * 0.15]
        
        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.grey),
            ("TEXTCOLOR", (0,0), (-1,0), colors.whitesmoke),
            ("ALIGN", (0,0), (-1,-1), "LEFT"),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0,0), (-1,-1), 12),
            ("TOPPADDING", (0,0), (-1,-1), 12),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 1, colors.black),
        ]))
        elements.append(table)
    else:
        elements.append(Paragraph("No major issues found!", styles["Normal"]))

    try:
        doc.build(elements)
        app.logger.info(f"[pdf] Build complete: {output_path}")
        return output_path
    except Exception as e:
        app.logger.error(f"Error generating PDF report: {str(e)}\n{traceback.format_exc()}")
        return None

@app.route('/')
def index():
    app.logger.info("Rendering index page")
    return render_template('index.html')

@app.route('/url-test', methods=['GET', 'POST'])
def url_test():
    if request.method == 'POST':
        urls = request.form.getlist('urls')
        if not urls or not any(urls):
            flash('Please enter at least one URL', 'error')
            return redirect(url_for('url_test'))
        
        # Filter out empty URLs and add https:// prefix if missing
        processed_urls = []
        for url in urls:
            url = url.strip()
            if url:
                if not url.startswith('http://') and not url.startswith('https://'):
                    url = 'https://' + url
                processed_urls.append(url)
        
        if not processed_urls:
            flash('Please enter at least one valid URL', 'error')
            return redirect(url_for('url_test'))
            
        try:
            # Create screenshots directory if it doesn't exist
            screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'screenshots')
            os.makedirs(screenshots_dir, exist_ok=True)
            
            # Run the scan directly with screenshots_dir instead of temp_dir
            issues, page_screenshots = run_scan(processed_urls, screenshots_dir)
            
            # Generate report ID
            report_id = str(uuid.uuid4())
            report_path = os.path.join(app.config['REPORTS_FOLDER'], f"{report_id}.pdf")
            
            # Generate PDF report
            generate_pdf_report(issues, page_screenshots, report_path)
            
            return render_template('results.html', 
                                issues=issues, 
                                screenshots=page_screenshots, 
                                report_id=report_id)
        except Exception as e:
            app.logger.error(f"Error processing URLs: {str(e)}")
            flash(f"An error occurred while processing your request: {str(e)}", 'error')
            return redirect(url_for('url_test'))
    
    return render_template('url_test.html')

@app.route('/html-test', methods=['GET', 'POST'])
def html_test():
    if request.method == 'POST':
        try:
            # Check if the post request has the file part
            if 'html_file' not in request.files:
                flash('No file part', 'error')
                return redirect(request.url)
            
            file = request.files['html_file']
            
            # If user does not select file, browser also
            # submit an empty part without filename
            if file.filename == '':
                flash('No selected file', 'error')
                return redirect(request.url)
            
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                
                # Create a temporary HTML file URL
                file_url = f"file://{file_path}"
                
                # Create screenshots directory if it doesn't exist
                screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'screenshots')
                os.makedirs(screenshots_dir, exist_ok=True)
                
                # Run the scan
                issues, page_screenshots = run_scan([file_url], screenshots_dir)
                
                # Generate report ID
                report_id = str(uuid.uuid4())
                report_path = os.path.join(app.config['REPORTS_FOLDER'], f"{report_id}.pdf")
                
                # Generate PDF report
                pdf_path = generate_pdf_report(issues, page_screenshots, report_path)
                if pdf_path is None:
                    flash('Error generating PDF report', 'error')
                    return redirect(request.url)
                
                return render_template('results.html', 
                                    issues=issues, 
                                    screenshots=page_screenshots, 
                                    report_id=report_id)
            else:
                flash('File type not allowed. Please upload HTML files only.', 'error')
                return redirect(request.url)
        except Exception as e:
            app.logger.error(f"Error in HTML test: {str(e)}")
            flash(f"An error occurred: {str(e)}", 'error')
            return redirect(request.url)
    
    return render_template('html_test.html')

@app.route('/download-report/<report_id>')
def download_report(report_id):
    try:
        report_path = os.path.join(app.config['REPORTS_FOLDER'], f"{report_id}.pdf")
        if os.path.exists(report_path):
            return send_file(report_path, as_attachment=True, download_name=f"usability-report-{report_id}.pdf")
        else:
            app.logger.error(f"Report not found: {report_id}")
            flash("Report not found", "error")
            return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Error downloading report: {str(e)}")
        flash(f"Error downloading report: {str(e)}", "error")
        return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)