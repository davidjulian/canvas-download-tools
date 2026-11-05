"""Local, bounded traversal of the Moderate student paginator only.

No Canvas APIs or private endpoints. Only recognized page controls are clicked.
Names and filter values stay in this process; the manifest stores hashes/counts.
"""
import json
import re
import time
from dataclasses import dataclass

import quick_capture as capture


ROOTS = ('nav[aria-label*="pagin" i],[role="navigation"][aria-label*="pagin" i],'
         'nav[aria-label*="page" i],[role="navigation"][aria-label*="page" i],'
         '[data-cid="Pagination"],[data-testid*="pagination" i],'
         '[class~="pagination"],[class~="Pagination"]')
BUTTONS = 'a,button,[role="button"],[role="link"]'
PAGE_NUMBER = re.compile(r"^(?:(?:go to )?(?:current )?page\s*)?(\d+)(?:\s*[,:(-]?\s*(?:current(?: page)?|page|of \d+)\)?)?$", re.I)
PROBE = r"""body => {
 const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&getComputedStyle(e).visibility!=='hidden'};
 const clean=s=>(s||'').replace(/\s+/g,' ').trim();
 const label=e=>clean(e.getAttribute('aria-label') ||
   (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ') || e.textContent);
 const number=e=>{let s=label(e), m=s.match(/^(?:(?:go to )?(?:current )?page\s*)?(\d+)(?:\s*[,:(-]?\s*(?:current(?: page)?|page|of \d+)\)?)?$/i);
   return m?Number(m[1]):null;};
 const disabled=e=>e.matches(':disabled') || e.getAttribute('aria-disabled')==='true' ||
   !!e.closest('[aria-disabled="true"],li.disabled');
 const isLog=e=>/^view\s+log\b/i.test(label(e))||/^view\s+log$/i.test(clean(e.textContent));
 const containsLogs=e=>[...e.querySelectorAll(CONTROL_SELECTOR)].some(isLog);
 const all=[...body.querySelectorAll(ROOT_SELECTOR)];
 // Canvas can label the navigation around its entire student table as Pages.
 // Such a container is not the student paginator, even if its rows have numbers.
 const pageLike=e=>[...e.querySelectorAll(CONTROL_SELECTOR)].some(c=>number(c)!==null||/^(?:(?:go to )?(?:next|previous|prev|first|last)(?:\s+page)?|[<>»›«‹←→])$/i.test(label(c))) ||
   e.matches('[data-cid="Pagination"],[data-testid*="pagination" i],[class~="pagination"],[class~="Pagination"]');
 const eligible=all.filter(e=>visible(e)&&!containsLogs(e)&&pageLike(e));
 const roots=eligible.filter(e=>!eligible.some(other=>other!==e&&other.contains(e)));
 const pagers=roots.map(root=>{
   const controls=[...root.querySelectorAll(CONTROL_SELECTOR)].filter(visible);
   const items=controls.map(e=>({index:[...root.querySelectorAll(CONTROL_SELECTOR)].indexOf(e),
      number:number(e), disabled:disabled(e), label:label(e)}));
   const marked=[...root.querySelectorAll('[aria-current="page"],[aria-current="true"],[aria-selected="true"],[data-selected="true"],li.active,li.current,.page-item.active')]
     .filter(visible).map(e=>number(e)??number(e.querySelector(CONTROL_SELECTOR)||e)).filter(n=>n!==null);
   const current=[...new Set(marked)];
   if(!current.length){const inactive=items.filter(x=>x.number!==null&&x.disabled);if(inactive.length===1)current.push(inactive[0].number);}
   const next=items.filter(x=>/^(?:go to )?next(?:\s+page)?(?:\s*[>»›→])?$/i.test(x.label)||/^[>»›→]$/.test(x.label));
   const previous=items.filter(x=>/^(?:go to )?(?:previous|prev)(?:\s+page)?(?:\s*[<«‹←])?$/i.test(x.label)||/^[<«‹←]$/.test(x.label));
   return {root:all.indexOf(root),current:current.length===1?current[0]:null,items,
     selectedMarkers:marked.length,
     next,previous,trailingEllipsis:/(?:…|\.{2,})\s*$/.test(root.textContent)};
 });
 // Only hashed locally. Do not inspect storage, network, hidden inputs or URLs.
 const fields=[...body.querySelectorAll('input,select,[role="combobox"]')]
   .filter(e=>visible(e)&&!e.closest(ROOT_SELECTOR)&&!e.closest('table,[role="table"],[role="grid"],[role="row"],tr')&&!e.matches('[type="hidden"]'))
   .map(e=>[e.tagName,e.getAttribute('type'),e.getAttribute('name'),e.getAttribute('aria-label'),
     e.value??clean(e.textContent),e.type==='checkbox'||e.type==='radio'?e.checked:null]);
 const heading=[...body.querySelectorAll('h1')].map(e=>clean(e.textContent));
 const hints=[...body.querySelectorAll(CONTROL_SELECTOR)].filter(visible)
   .filter(e=>!e.closest('table,[role="table"],[role="grid"],[role="row"],tr'))
   .some(e=>/^(?:(?:go to )?(?:current )?page\s*\d+|(?:go to )?(?:next|previous) page)$/i.test(label(e))||
     (e.hasAttribute('aria-current')&&number(e)!==null)) ||
   [...body.querySelectorAll('nav,[role="navigation"]')].filter(e=>visible(e)&&!containsLogs(e))
     .some(e=>[...e.querySelectorAll(CONTROL_SELECTOR)].filter(c=>visible(c)&&number(c)!==null).length>1);
 const moderate=[...body.querySelectorAll('h1,h2,[role="tab"][aria-selected="true"]')]
   .some(e=>visible(e)&&/^Moderate\b/i.test(clean(e.textContent)));
 return {pagers,context:JSON.stringify([heading,fields]),hints,moderate,
   excludedListRoots:all.filter(e=>visible(e)&&containsLogs(e)).length,
   excludedNonPageRoots:all.filter(e=>visible(e)&&!containsLogs(e)&&!pageLike(e)).length};
}""".replace('ROOT_SELECTOR', json.dumps(ROOTS)).replace('CONTROL_SELECTOR', json.dumps(BUTTONS))


def control_label(item):
    label = item.get_attribute('aria-label') or ''
    if not label:
        # The locator snapshot uses the computed accessible name, including
        # labels inside open shadow roots; a body DOM query cannot see those.
        snapshot = item.aria_snapshot(timeout=3000).splitlines()
        match = re.match(r'^\s*- (?:button|link|combobox) "([^"]*)"', snapshot[0] if snapshot else '')
        label = match[1] if match else capture.element_text(item)
    return ' '.join(label.split())


def refine_pagers(frame, probe):
    """Recognize empty/single-page widgets and use locator access when needed."""
    roots = frame.locator(ROOTS)
    pagers, ignored, page_size_only = [], 0, 0
    for original in probe['pagers']:
        pager = dict(original)
        root = None
        for index in range(roots.count()):
            candidate = roots.nth(index)
            if candidate.evaluate('(e, p) => e === e.ownerDocument.body.querySelectorAll(p.selector)[p.index]',
                                  {'selector': ROOTS, 'index': original['root']}):
                root, pager['root'] = candidate, index
                break
        if root is None:
            raise capture.CaptureError('moderate_pagination_list_not_ready')
        if pager['current'] is not None:
            pagers.append(pager)
            continue
        items, next_items, previous = [], [], []
        controls = root.locator(BUTTONS)
        for index in range(controls.count()):
            item = controls.nth(index)
            if not item.is_visible():
                continue
            label = control_label(item)
            match = PAGE_NUMBER.fullmatch(label)
            if not match:
                match = PAGE_NUMBER.fullmatch(' '.join(capture.element_text(item).split()))
            info = {'index': index, 'number': int(match[1]) if match else None,
                    'disabled': not item.is_enabled(), 'label': label}
            items.append(info)
            if re.fullmatch(r'(?:go to )?next(?:\s+page)?|[>»›→]', label, re.I):
                next_items.append(info)
            if re.fullmatch(r'(?:go to )?(?:previous|prev)(?:\s+page)?|[<«‹←]', label, re.I):
                previous.append(info)
        marker = root.locator('[aria-current="page"],[aria-current="true"],[aria-selected="true"],[data-selected="true"],li.active,li.current,.page-item.active')
        selected = []
        for index in range(marker.count()):
            item = marker.nth(index)
            if item.is_visible():
                match = PAGE_NUMBER.fullmatch(control_label(item)) or PAGE_NUMBER.fullmatch(' '.join(capture.element_text(item).split()))
                if match:
                    selected.append(int(match[1]))
        disabled_numbers = [i['number'] for i in items if i['number'] is not None and i['disabled']]
        current = set(selected or (disabled_numbers if len(disabled_numbers) == 1 else []))
        page_text = ' '.join(capture.element_text(root).split())
        number_text = root.get_by_text(re.compile(r'^\s*\d+\s*$'))
        indicators = {int(capture.element_text(number_text.nth(i)).strip()) for i in range(number_text.count())
                      if number_text.nth(i).is_visible()}
        operative = any(i['number'] is not None for i in items) or bool(next_items or previous)
        size_fields = root.locator('select,[role="combobox"],[aria-haspopup="listbox"]')
        size_labels = [control_label(size_fields.nth(i)) for i in range(size_fields.count()) if size_fields.nth(i).is_visible()]
        size_text = page_text + ' ' + ' '.join(size_labels)
        if (size_labels and not next_items and not previous and not current and len(indicators) <= 1
                and re.search(r'per\s+page|page\s+size', size_text, re.I)):
            page_size_only += 1
            continue
        if not operative and not current:
            whole_range = re.fullmatch(r'(?:showing\s+)?1\s*[-–—]\s*(\d+)\s+of\s+(\d+)(?:\s+(?:students|items|results|entries))?', page_text, re.I)
            if re.fullmatch(r'(?:page\s*)?1(?:\s*(?:of|/)\s*1)?', page_text, re.I):
                current = {1}
            elif whole_range and int(whole_range[1]) == int(whole_range[2]):
                current = {1}
            elif not indicators and not re.search(r'\d', page_text):
                # Canvas renders an empty pagination wrapper for a small class.
                # Its presence alone does not mean that additional pages exist.
                ignored += 1
                continue
        pager.update(items=items, next=next_items, previous=previous,
                     current=next(iter(current)) if len(current) == 1 else None,
                     selectedMarkers=len(selected), locatorFallback=True,
                     visibleNumberIndicators=len(indicators))
        pagers.append(pager)
    probe['pagers'] = pagers
    probe['ignoredEmptyPagers'] = ignored
    probe['ignoredPageSizePagers'] = page_size_only
    return probe


@dataclass
class ListPage:
    number: int
    signature: tuple
    names: tuple
    offset: int = 0


class ModeratePages:
    def __init__(self, page, check_cancel=lambda: None, progress=lambda **_: None,
                 timeout=30, stable_seconds=.5, max_pages=1000, max_logs=5000):
        self.page, self.check_cancel, self.progress = page, check_cancel, progress
        self.timeout, self.stable_seconds = timeout, stable_seconds
        self.max_pages, self.max_logs = max_pages, max_logs
        self.context_hash = None
        self.paginated = None
        self.plans = []
        self.diagnostic = []

    def read(self):
        candidates = []
        empty = []
        self.diagnostic = []
        inspection_failed = False
        for frame_number, frame in enumerate(self.page.frames, 1):
            info = {"frame_number": frame_number}
            self.diagnostic.append(info)
            try:
                info['visible'] = capture.visible_frame(frame)
                if not info['visible']:
                    continue
                controls, signature = capture.controls_in_frame(frame)
                info['view_log_controls'] = len(controls)
                info['body_present'] = bool(frame.locator('body').count())
                if not info['body_present']:
                    continue
                probe = refine_pagers(frame, frame.locator('body').evaluate(PROBE))
                info.update(pagination_roots=len(probe['pagers']),
                            excluded_student_list_roots=probe['excludedListRoots'],
                            excluded_navigation_roots=probe['excludedNonPageRoots'],
                            ignored_empty_pagination_roots=probe['ignoredEmptyPagers'],
                            ignored_page_size_only_roots=probe['ignoredPageSizePagers'],
                            unrecognized_page_control_hint=probe['hints'],
                            moderate_marker=probe['moderate'],
                            pagers=[{'current_page_recognized': p['current'] is not None,
                                     'selected_markers': p['selectedMarkers'],
                                     'visible_number_indicators': p.get('visibleNumberIndicators'),
                                     'locator_fallback_used': p.get('locatorFallback', False),
                                     'numbered_controls': sum(x['number'] is not None for x in p['items']),
                                     'next_controls': len(p['next']), 'previous_controls': len(p['previous'])}
                                    for p in probe['pagers']])
            except Exception as error:
                info['inspection_error_category'] = capture.safe_error_category(error)
                if info.get('view_log_controls'):
                    inspection_failed = True
                continue
            if controls:
                candidates.append((frame, controls, signature, probe))
            elif probe['pagers'] and (self.context_hash == capture.digest(probe['context']) or
                                     (self.context_hash is None and probe['moderate'])):
                empty.append((frame, controls, signature, probe))
        candidates = candidates or empty
        if len(candidates) != 1:
            if inspection_failed:
                raise capture.CaptureError('moderate_pagination_inspection_failed')
            raise capture.CaptureError('moderate_pagination_list_not_ready')
        frame, controls, signature, probe = candidates[0]
        context = capture.digest(probe['context'])
        if self.context_hash is not None and context != self.context_hash:
            raise capture.CaptureError('moderate_filters_changed')
        pagers = probe['pagers']
        if not pagers:
            if probe['hints'] or self.paginated:
                raise capture.CaptureError('moderate_pagination_not_recognized')
            pager = None
            number = 1
        else:
            # Top/bottom copies are allowed only if their state agrees.
            keys = {(p['current'], tuple((x['number'], x['disabled']) for x in p['items']),
                     tuple(x['disabled'] for x in p['next']), tuple(x['disabled'] for x in p['previous']))
                    for p in pagers}
            if len(keys) != 1 or pagers[0]['current'] is None:
                raise capture.CaptureError('moderate_pagination_not_recognized')
            pager = pagers[0]
            number = pager['current']
            if not 1 <= number <= self.max_pages:
                raise capture.CaptureError('moderate_pagination_limit')
        if self.paginated is False and pager:
            raise capture.CaptureError('moderate_pagination_changed')
        return frame, controls, signature, pager, number, context

    def ready(self, expected_number=None, previous_signature=None):
        deadline = time.monotonic() + self.timeout
        stable, since = None, None
        while time.monotonic() < deadline:
            self.check_cancel()
            try:
                key = capture.list_quiet_token(self.page)
                busy = capture.visibly_busy(self.page)
                if not busy and key == stable and time.monotonic() - since >= self.stable_seconds:
                    # Scan all identities once after the cheap quiet check. If
                    # rows changed during that scan, discard it and wait again.
                    result = self.read()
                    _, _, signature, _, number, _ = result
                    ok = expected_number is None or number == expected_number
                    if previous_signature and signature == previous_signature:
                        ok = False
                    if ok and key == capture.list_quiet_token(self.page) and not capture.visibly_busy(self.page):
                        return result
                    stable, since = None, None
                elif busy or key != stable:
                    stable, since = (None, None) if busy else (key, time.monotonic())
            except capture.CaptureError as error:
                if str(error) != 'moderate_pagination_list_not_ready':
                    raise
                stable, since = None, None
            except Exception:
                stable, since = None, None
            self.page.wait_for_timeout(100)
        raise capture.CaptureError('moderate_pagination_did_not_settle')

    @staticmethod
    def target(pager, current, destination):
        if pager is None:
            raise capture.CaptureError('moderate_pagination_not_recognized')
        numbered = [x for x in pager['items'] if x['number'] == destination and not x['disabled']]
        if len(numbered) == 1:
            return numbered[0]
        direction = pager['next'] if destination == current + 1 else pager['previous'] if destination == current - 1 else []
        enabled = [x for x in direction if not x['disabled']]
        if len(enabled) != 1:
            raise capture.CaptureError('moderate_pagination_not_recognized')
        return enabled[0]

    def click(self, current_state, destination):
        self.check_cancel()
        frame, _, signature, pager, current, _ = current_state
        target = self.target(pager, current, destination)
        before = set(self.page.context.pages)
        frame.locator(ROOTS).nth(pager['root']).locator(BUTTONS).nth(target['index']).click(timeout=5000)
        if set(self.page.context.pages) != before:
            raise capture.CaptureError('new_tab_navigation_not_supported')
        return self.ready(destination, previous_signature=signature)

    def goto(self, destination):
        state = self.ready()
        visited = set()
        while state[4] != destination:
            self.check_cancel()
            current, pager = state[4], state[3]
            if current in visited:
                raise capture.CaptureError('moderate_pagination_repeated')
            visited.add(current)
            direct = pager and any(x['number'] == destination and not x['disabled'] for x in pager['items'])
            target = destination if direct else current + (1 if current < destination else -1)
            state = self.click(state, target)
        return state

    def has_next(self, state):
        pager, current = state[3:5]
        if pager is None:
            return False
        higher = any(x['number'] is not None and x['number'] > current for x in pager['items'])
        if pager['next']:
            if len(pager['next']) != 1 or (pager['next'][0]['disabled'] and higher):
                raise capture.CaptureError('moderate_pagination_not_recognized')
            return not pager['next'][0]['disabled']
        if higher:
            self.target(pager, current, current + 1)
            return True
        if pager['trailingEllipsis']:
            raise capture.CaptureError('moderate_pagination_not_recognized')
        return False

    def discover(self):
        first = self.ready()
        self.context_hash, self.paginated = first[5], first[3] is not None
        state = self.goto(1) if first[4] != 1 else first
        seen, offset = set(), 0
        while True:
            self.check_cancel()
            _, controls, signature, _, number, _ = state
            if number != len(self.plans) + 1 or any(item in seen for item in signature):
                raise capture.CaptureError('moderate_pagination_repeated')
            if len(set(signature)) != len(signature):
                raise capture.CaptureError('moderate_pagination_repeated')
            seen.update(signature)
            names = tuple(capture.proposed_student_name(item) for item in controls)
            self.plans.append(ListPage(number, signature, names, offset))
            offset += len(signature)
            self.progress(current_page=number, page_count=len(self.plans), discovered_logs=offset)
            if len(self.plans) > self.max_pages or offset > self.max_logs:
                raise capture.CaptureError('moderate_pagination_limit')
            if not self.has_next(state):
                break
            state = self.click(state, number + 1)
        if not offset:
            raise capture.CaptureError('no_view_log_controls_found')
        self.goto(1)
        self.verify(self.plans[0])
        return self.plans

    def verify(self, plan, state=None):
        state = state if state is not None else self.ready()
        if state[4] != plan.number or state[2] != plan.signature:
            raise capture.CaptureError('moderate_list_changed')
        if self.has_next(state) != (plan.number < len(self.plans)):
            raise capture.CaptureError('moderate_pagination_changed')
        return state

    def restore_after_log(self, plan):
        self.check_cancel()
        try:
            self.page.evaluate('() => { window.history.back(); }')
        except Exception:
            pass  # Navigation can destroy the evaluation context.
        state = self.ready()
        if state[4] != plan.number:
            state = self.goto(plan.number)
        return self.verify(plan, state)


def moderate_fingerprint(page):
    """Tab selection can recognize a marked empty Moderate student page too."""
    try:
        controls, signature = capture.controls_on_page(page)
        return len(controls), signature
    except capture.CaptureError as error:
        if str(error) != 'no_view_log_controls_found':
            raise
    _, controls, signature, _, number, context = ModeratePages(page).read()
    return len(controls), (number, signature, context)


def pagination_diagnostic(context):
    """Only counts, flags and fixed error categories; never names/text/URLs."""
    tabs = []
    for number, page in enumerate(context.pages, 1):
        navigator = ModeratePages(page)
        try:
            navigator.read()
            category = 'readable'
        except capture.CaptureError as error:
            category = str(error)
        except Exception as error:
            category = capture.safe_error_category(error)
        tabs.append({'tab_number': number, 'result_category': category, 'frames': navigator.diagnostic})
    return {'version': capture.VERSION, 'tabs': tabs}
