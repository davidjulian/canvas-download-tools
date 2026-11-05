"""Invented multi-page Moderate lists; no network or real profile access."""
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quick_capture as capture
import log_app as web
from moderate_pages import ModeratePages, moderate_fingerprint, pagination_diagnostic


FIXTURE = r'''<!doctype html><html><body><main></main><script>
window.size=2;window.pages=3;window.empty=[];window.noNext=false;window.resetBack=false;
window.bad=false;window.duplicate=false;window.filterChange=false;window.stale=false;
window.mirror=false;window.mismatch=false;window.logClicks=0;window.changes=[];
function pageNumber(){return Number(new URLSearchParams(location.hash.slice(1)).get('p')||1)}
function go(p){changes.push(p);history.replaceState({},'',`#p=${p}`);draw()}
function openLog(n){logClicks++;history.pushState({},'',`#p=${pageNumber()}&log=${n}`);draw()}
function draw(){
 let p=pageNumber(), n=Number(new URLSearchParams(location.hash.slice(1)).get('log'));
 if(n){document.querySelector('main').innerHTML=`<h1>Log of student taking events</h1><p>Began Tuesday at 9 AM</p><h2>Elapsed time</h2><p>00:00 Session started.</p><p>00:10 Stopped viewing the Canvas quiz-taking page...</p><p>00:30 Resumed.</p><p>01:00 Quiz submitted.</p><p>Synthetic final event ${n}</p>`;return;}
 let actual=(duplicate||stale)?1:p;
 let ids=empty.includes(p)?[]:Array.from({length:size},(_,i)=>(actual-1)*size+i+1);
 let buttons=Array.from({length:pages},(_,i)=>`<button ${!bad&&p===i+1?'aria-current="page"':''} aria-label="Page ${i+1}" onclick="go(${i+1})">${i+1}</button>`).join('');
 let nav=`<nav aria-label="Student pagination">${noNext?'':`<button aria-label="Previous page" ${p===1?'disabled':''} onclick="go(${p-1})">Previous</button>`}${buttons}${noNext?'':`<button aria-label="Next page" ${p===pages?'disabled':''} onclick="go(${p+1})">Next</button>`}</nav>`;
 document.querySelector('main').innerHTML='<h1>Moderate invented quiz</h1><input aria-label="Search students" value="'+(filterChange&&p>1?'Changed':'')+'"><select aria-label="Students per page"><option>50</option></select><table>'+ids.map(id=>`<tr><td>Example Student ${id}</td><td><a href="#p=${p}&log=${id}" onclick="event.preventDefault();openLog(${id})">View Log</a></td></tr>`).join('')+'</table>'+nav+(mirror?nav.replaceAll('Student pagination',mismatch?'Other pagination':'Student pagination'):'');
 if(mismatch)document.querySelectorAll('nav')[1]?.querySelector('[aria-current]')?.removeAttribute('aria-current');
}window.addEventListener('popstate',()=>{if(resetBack)history.replaceState({},'','#p=1');draw()});draw();
</script></body></html>'''


@unittest.skipUnless(os.environ.get('CANVAS_RUN_BROWSER_TESTS') == '1', 'optional synthetic browser test')
class ModeratePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, channel=os.environ.get(
            'CANVAS_TEST_BROWSER', 'msedge' if sys.platform == 'win32' else 'chrome'))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body=FIXTURE)
                           if route.request.url.split('#')[0] == 'https://moderate-fixture.invalid/' else route.abort())
        self.page = self.context.new_page()
        self.page.goto('https://moderate-fixture.invalid/')
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.context.close()
        self.temp.cleanup()

    def navigator(self, **options):
        return ModeratePages(self.page, timeout=3, stable_seconds=.01, **options)

    def test_discovers_150_logs_from_page_three_without_opening_any_log(self):
        self.page.evaluate('size=50;go(3)')
        plans = ModeratePages(self.page, timeout=60, stable_seconds=.01).discover()
        self.assertEqual([p.number for p in plans], [1, 2, 3])
        self.assertEqual([p.offset for p in plans], [0, 50, 100])
        self.assertEqual(sum(len(p.signature) for p in plans), 150)
        self.assertEqual(plans[2].names[0][0], 'Example Student 101')
        self.assertEqual(self.page.evaluate('pageNumber()'), 1)
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def test_numbered_controls_without_next_and_mirrored_paginators(self):
        self.page.evaluate('noNext=true;mirror=true;draw()')
        plans = self.navigator().discover()
        self.assertEqual(len(plans), 3)
        self.assertEqual(sum(len(p.signature) for p in plans), 6)

    def test_navigation_wrapping_students_is_not_a_single_page_paginator(self):
        self.page.evaluate('''pages=1;draw();document.querySelector('main nav').remove();
          const main=document.querySelector('main'), outer=document.createElement('nav');
          outer.setAttribute('aria-label','Quiz pages');main.replaceWith(outer);outer.append(main);
          document.querySelectorAll('tr').forEach(e=>e.insertAdjacentHTML('beforeend','<td><a href="#attempt">1</a></td>'));''')
        nav = self.navigator()
        plans = nav.discover()
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(plans[0].signature), 2)
        self.assertGreater(nav.diagnostic[0]['excluded_student_list_roots'], 0)

    def test_student_wrapper_does_not_hide_the_nested_real_paginator(self):
        self.page.evaluate('''const main=document.querySelector('main'), outer=document.createElement('nav');
          outer.setAttribute('aria-label','Quiz pages');main.replaceWith(outer);outer.append(main);''')
        plans = self.navigator().discover()
        self.assertEqual(len(plans), 3)
        self.assertEqual(sum(len(p.signature) for p in plans), 6)

    def test_quiz_page_navigation_without_numbered_controls_is_not_a_paginator(self):
        self.page.evaluate('''pages=1;draw();document.querySelector('main nav').remove();
          document.body.insertAdjacentHTML('afterbegin','<nav aria-label="Quiz page navigation"><a aria-current="page">Moderate</a><a>Build</a><a>Reports</a></nav>');''')
        nav = self.navigator()
        self.assertEqual(len(nav.discover()), 1)
        self.assertEqual(nav.diagnostic[0]['pagination_roots'], 0)
        self.assertGreater(nav.diagnostic[0]['excluded_navigation_roots'], 0)

    def test_empty_pagination_placeholder_is_a_single_student_page(self):
        self.page.evaluate('''pages=1;draw();const pager=document.querySelector('main nav');
          pager.dataset.cid='Pagination';pager.innerHTML='';pager.style.height='24px';''')
        nav = self.navigator()
        plans = nav.discover()
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(plans[0].signature), 2)
        self.assertEqual(nav.diagnostic[0]['ignored_empty_pagination_roots'], 1)

    def test_static_single_page_indicator_does_not_require_a_clickable_button(self):
        self.page.evaluate('pages=1;draw();const p=document.querySelector("main nav");p.dataset.cid="Pagination";p.innerHTML="<span>1</span>"')
        nav = self.navigator()
        self.assertEqual(len(nav.discover()), 1)
        self.assertEqual(nav.diagnostic[0]['pagers'][0]['current_page_recognized'], True)

    def test_page_size_selector_alone_does_not_imply_more_student_pages(self):
        self.page.evaluate('''pages=1;draw();const p=document.querySelector('main nav');p.dataset.cid='Pagination';
          p.innerHTML='<label>Students per page<select aria-label="Students per page"><option>50</option></select></label>';''')
        nav = self.navigator()
        self.assertEqual(len(nav.discover()), 1)
        self.assertEqual(nav.diagnostic[0]['ignored_page_size_only_roots'], 1)

    def test_noninteractive_multiple_page_numbers_are_not_silently_ignored(self):
        self.page.evaluate('''draw();const p=document.querySelector('main nav');p.dataset.cid='Pagination';
          p.innerHTML='<span>1</span><span>2</span><span>3</span>';''')
        with self.assertRaisesRegex(capture.CaptureError, 'moderate_pagination_not_recognized'):
            self.navigator().discover()

    def test_whole_list_range_status_can_identify_a_single_page(self):
        self.page.evaluate('''pages=1;draw();const p=document.querySelector('main nav');p.dataset.cid='Pagination';
          p.textContent='1–2 of 2';''')
        self.assertEqual(len(self.navigator().discover()), 1)

    def test_numbered_pagination_inside_open_shadow_root_is_read_by_locators(self):
        self.page.evaluate('''const originalDraw=draw;draw=()=>{originalDraw();
          const pager=document.querySelector('main nav');if(!pager)return;
          const host=document.createElement('div');host.dataset.cid='Pagination';
          host.attachShadow({mode:'open'}).innerHTML=pager.innerHTML;pager.replaceWith(host);
        };draw()''')
        plans = self.navigator().discover()
        self.assertEqual(len(plans), 3)
        self.assertEqual(sum(len(p.signature) for p in plans), 6)

    def test_unrelated_visible_frame_inspection_error_does_not_discard_student_list(self):
        real = self.page.main_frame
        unrelated = MagicMock()
        unrelated.locator.return_value.count.return_value = 1
        unrelated.locator.return_value.evaluate.side_effect = RuntimeError('PRIVATE_UNRELATED_FRAME_ERROR')
        page = MagicMock()
        page.frames = [real, unrelated]
        controls, signature = capture.controls_in_frame(real)
        with patch.object(capture, 'visible_frame', return_value=True), \
                patch.object(capture, 'controls_in_frame', side_effect=lambda frame: (controls, signature) if frame is real else ([], ())):
            nav = ModeratePages(page)
            self.assertEqual(nav.read()[2], signature)
        self.assertEqual(nav.diagnostic[1]['inspection_error_category'], 'browser_inspection_error')
        self.assertNotIn('PRIVATE_UNRELATED_FRAME_ERROR', json.dumps(nav.diagnostic))

    def test_diagnostic_has_counts_and_categories_without_student_text(self):
        self.page.evaluate('bad=true;draw()')
        report = pagination_diagnostic(self.context)
        serialized = json.dumps(report)
        self.assertNotIn('Example Student', serialized)
        self.assertNotIn('moderate-fixture.invalid', serialized)
        self.assertNotIn('Moderate invented quiz', serialized)
        self.assertEqual(report['tabs'][0]['result_category'], 'moderate_pagination_not_recognized')
        self.assertEqual(report['tabs'][0]['frames'][0]['view_log_controls'], 2)

    def test_empty_middle_page_does_not_end_discovery(self):
        self.page.evaluate('empty=[2];draw()')
        plans = self.navigator().discover()
        self.assertEqual([len(p.signature) for p in plans], [2, 0, 2])
        self.assertEqual(plans[2].offset, 2)

    def test_can_start_on_marked_empty_page_and_scan_earlier_students(self):
        self.page.evaluate('empty=[3];go(3)')
        chosen = capture.choose_moderate_page(self.context, timeout=3, stable_seconds=.01,
                                              candidate_fingerprint=moderate_fingerprint)
        self.assertIs(chosen, self.page)
        plans = self.navigator().discover()
        self.assertEqual([len(p.signature) for p in plans], [2, 2, 0])
        self.assertEqual(self.page.evaluate('pageNumber()'), 1)

    def test_unrecognized_navigation_number_group_is_not_silently_single_page(self):
        self.page.evaluate("document.querySelector('nav').setAttribute('aria-label','Unknown controls')")
        with self.assertRaisesRegex(capture.CaptureError, 'pagination_not_recognized'):
            self.navigator().discover()

    def test_instui_markers_compact_pages_and_hidden_disabled_arrows(self):
        # The public InstUI source uses data-cid=Pagination, aria-current=page,
        # a sliding number window and omits disabled arrows by default.
        self.page.evaluate(r'''pages=8;const originalDraw=draw;draw=()=>{originalDraw();
          const root=document.querySelector('nav');if(!root)return;
          root.removeAttribute('aria-label');root.setAttribute('role','navigation');root.dataset.cid='Pagination';
          root.querySelectorAll('button:disabled').forEach(e=>e.remove());
          root.querySelectorAll('[aria-label^="Page "]').forEach(e=>{
            let n=Number(e.textContent);if(n!==1&&n!==pages&&Math.abs(n-pageNumber())>1)e.remove();
          });
          if(pageNumber()>3)root.querySelector('[aria-label="Page 1"]').insertAdjacentHTML('afterend','<span>…</span>');
          if(pageNumber()<pages-2)root.querySelector(`[aria-label="Page ${pages}"]`).insertAdjacentHTML('beforebegin','<span>…</span>');
        };go(7)''')
        plans = self.navigator().discover()
        self.assertEqual(len(plans), 8)
        self.assertEqual(sum(len(p.signature) for p in plans), 16)

    def test_visible_numeric_current_page_can_be_noninteractive(self):
        self.page.evaluate("draw();document.querySelector('[aria-current]').outerHTML='<span aria-current=page>1</span>'")
        self.assertEqual(self.navigator().ready()[4], 1)

    def test_unrecognized_selection_or_conflicting_pagers_stops(self):
        for script in ('bad=true;draw()', 'bad=false;mirror=true;mismatch=true;draw()'):
            with self.subTest(script=script):
                self.page.evaluate(script)
                with self.assertRaisesRegex(capture.CaptureError, 'pagination_not_recognized'):
                    self.navigator().discover()
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def test_paginator_advancing_without_new_rows_stops(self):
        # Identical row identity includes href, so use constant target hrefs too.
        self.page.evaluate("stale=true;const oldDraw=draw;draw=()=>{oldDraw();document.querySelectorAll('a').forEach(a=>a.setAttribute('href','#same-'+a.textContent))};draw()")
        with self.assertRaisesRegex(capture.CaptureError, 'pagination_did_not_settle'):
            self.navigator().discover()
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def test_repeated_attempt_identity_across_pages_stops(self):
        self.page.evaluate("duplicate=true;const oldDraw=draw;draw=()=>{oldDraw();document.querySelectorAll('a').forEach((a,i)=>a.setAttribute('href','#same-'+i))};draw()")
        with self.assertRaisesRegex(capture.CaptureError, 'pagination_did_not_settle'):
            self.navigator().discover()

    def test_filters_changing_during_discovery_stops(self):
        self.page.evaluate('filterChange=true;draw()')
        with self.assertRaisesRegex(capture.CaptureError, 'moderate_filters_changed'):
            self.navigator().discover()

    def test_row_checkboxes_are_not_confused_with_course_filters(self):
        self.page.evaluate(r'''const originalDraw=draw;draw=()=>{originalDraw();
          document.querySelectorAll('tr').forEach((e,i)=>e.insertAdjacentHTML('afterbegin',
            `<td><input type=checkbox name="student-${pageNumber()}-${i}"></td>`));
        };draw()''')
        self.assertEqual(len(self.navigator().discover()), 3)

    def test_changed_rows_or_new_pages_after_review_are_rejected(self):
        nav = self.navigator()
        plans = nav.discover()
        self.page.evaluate("document.querySelector('td').textContent='Changed invented student'")
        with self.assertRaisesRegex(capture.CaptureError, 'moderate_list_changed'):
            nav.verify(plans[0])
        self.page.evaluate('pages=4;draw()')
        nav.goto(3)
        with self.assertRaisesRegex(capture.CaptureError, 'moderate_pagination_changed'):
            nav.verify(plans[2])

    def test_back_reset_to_first_page_is_restored_to_reviewed_page(self):
        self.page.evaluate('resetBack=true;draw()')
        nav = self.navigator()
        plans = nav.discover()
        nav.goto(2)
        self.page.get_by_role('link', name='View Log').first.click()
        nav.restore_after_log(plans[1])
        self.assertEqual(self.page.evaluate('pageNumber()'), 2)
        self.assertEqual(capture.controls_on_page(self.page)[1], plans[1].signature)

    def test_iframe_student_pagination_and_joint_log_history(self):
        self.page.set_content('<h1>Canvas shell</h1><iframe style="width:900px;height:700px" src="https://moderate-fixture.invalid/"></iframe>')
        nav = self.navigator()
        plans = nav.discover()
        nav.goto(2)
        self.page.frames[1].get_by_role('link', name='View Log').first.click()
        nav.restore_after_log(plans[1])
        self.assertEqual(len(plans), 3)

    def test_limits_and_cancel_end_discovery_before_logs(self):
        with self.assertRaisesRegex(capture.CaptureError, 'pagination_limit'):
            self.navigator(max_logs=3).discover()
        self.page.evaluate('go(1)')
        def cancelled():
            raise capture.CaptureError('stopped_by_user')
        with self.assertRaisesRegex(capture.CaptureError, 'stopped_by_user'):
            self.navigator(check_cancel=cancelled).discover()
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def test_stable_wait_does_one_authoritative_scan_instead_of_one_per_poll(self):
        nav = self.navigator()
        with patch.object(nav, 'read', wraps=nav.read) as scan:
            state = nav.ready()
        self.assertEqual(len(state[2]), 2)
        self.assertEqual(scan.call_count, 1)

    def test_row_change_during_authoritative_scan_is_discarded_before_capture(self):
        nav = self.navigator()
        plan = nav.discover()[0]
        original = nav.read
        calls = []
        def changed():
            result = original()
            calls.append(True)
            if len(calls) == 1:
                self.page.evaluate("document.querySelector('td').textContent='Changed invented student'")
            return result
        with patch.object(nav, 'read', side_effect=changed), self.assertRaisesRegex(capture.CaptureError, 'moderate_list_changed'):
            nav.verify(plan)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def test_hidden_and_disabled_row_updates_do_not_repeat_the_full_scan(self):
        self.page.evaluate('''document.querySelector('table').insertAdjacentHTML('beforeend',
          '<tr hidden><td id="hidden-status">Old hidden status</td><td><button>View Log</button></td></tr>'+
          '<tr><td id="disabled-status">Old disabled status</td><td><button disabled>View Log</button></td></tr>');''')
        nav = self.navigator()
        original = nav.read
        def changed():
            result = original()
            self.page.evaluate("document.getElementById('hidden-status').textContent='New hidden status';document.getElementById('disabled-status').textContent='New disabled status'")
            return result
        with patch.object(nav, 'read', side_effect=changed) as scan:
            state = nav.ready()
        self.assertEqual(len(state[2]), 2)
        self.assertEqual(scan.call_count, 1)

    def test_verified_control_is_rechecked_if_target_changes_before_click(self):
        nav = self.navigator()
        plan = nav.discover()[0]
        verified = nav.verify(plan)
        self.page.evaluate("document.querySelector('td').textContent='Changed invented student'")
        collector = capture.QuickCapture(self.page, Path(self.temp.name) / 'target-check')
        with self.assertRaisesRegex(capture.CaptureError, 'moderate_list_changed'):
            collector.open_control(0, plan.signature, verified_controls=verified[1])
        self.assertEqual(self.page.evaluate('logClicks'), 0)

    def collector(self, mode='coded', stop_after=None):
        app = web.LocalApp(profile=Path(self.temp.name) / 'profile')
        app.update(run_id='invented')
        app.review_count = app.check_count = 0
        def action(phase, **values):
            app.update(phase=phase, **values)
            if phase == 'review':
                app.review_count += 1
                rows = app.snapshot()['names']
                self.assertEqual([r['moderate_page'] for r in rows], [1, 1, 2, 2, 3, 3])
                rows[-1]['name'] = 'Corrected Example Student 6'
                return 'start', {'names': rows}
            app.check_count += 1
            return 'checked', {}
        app.await_action = action
        if stop_after is not None:
            original = app.check_cancel
            def cancel():
                original()
                if app.snapshot()['saved'] >= stop_after:
                    raise capture.CaptureError('stopped_by_user')
            app.check_cancel = cancel
        collector = web.WebCapture(self.page, Path(self.temp.name) / mode, app, settle=0, mode=mode)
        return collector, app

    def fast_navigator(self, page, **options):
        return ModeratePages(page, timeout=3, stable_seconds=.01, **options)

    def test_coded_capture_all_pages_one_review_key_and_summary(self):
        collector, app = self.collector()
        with patch.object(web, 'ModeratePages', self.fast_navigator):
            collector.run()
        collector.bundle()
        self.assertEqual((app.review_count, app.check_count), (1, 1))
        self.assertEqual(app.snapshot()['saved'], 6)
        self.assertEqual([r['position'] for r in app.snapshot()['summary_rows']], list(range(1, 7)))
        self.assertTrue(collector.manifest['student_pagination_completed'])
        self.assertTrue(all(p['captured'] for p in collector.manifest['moderate_pages']))
        self.assertFalse(collector.manifest['all_students_verified'])
        with (collector.out / 'name_key.csv').open(encoding='utf-8-sig', newline='') as handle:
            key = list(csv.DictReader(handle))
        self.assertEqual([int(r['moderate_page']) for r in key], [1, 1, 2, 2, 3, 3])
        self.assertEqual(len({r['record_id'] for r in key}), 6)
        self.assertEqual(key[-1]['student_name'], 'Corrected Example Student 6')
        with zipfile.ZipFile(collector.run_out / 'coded-logs.zip') as archive:
            self.assertEqual(sum(n.endswith('.txt') and n.startswith('log_') for n in archive.namelist()), 6)
            shared = '\n'.join(archive.read(n).decode() for n in archive.namelist())
            self.assertNotIn('Example Student', shared)
            self.assertFalse(any('name_key' in n or 'summary' in n for n in archive.namelist()))
        self.assertEqual(self.page.evaluate('logClicks'), 6)

    def test_named_capture_all_pages_has_unique_positions_and_no_key(self):
        collector, app = self.collector('named')
        with patch.object(web, 'ModeratePages', self.fast_navigator):
            collector.run()
        collector.bundle()
        self.assertEqual(len(collector.manifest['saved_logs']), 6)
        self.assertEqual([r['control_position'] for r in collector.manifest['saved_logs']], list(range(1, 7)))
        self.assertEqual([r['moderate_page'] for r in collector.manifest['saved_logs']], [1, 1, 2, 2, 3, 3])
        self.assertFalse((collector.out / 'name_key.csv').exists())
        with zipfile.ZipFile(collector.run_out / 'named-logs.zip') as archive:
            self.assertIn('Student: Corrected Example Student 6', archive.read('log_006_Corrected_Example_Student_6.txt').decode())
        self.assertEqual((app.review_count, app.check_count), (1, 1))

    def test_stop_on_later_page_keeps_global_ids_summary_and_partial_zip(self):
        collector, app = self.collector(stop_after=3)
        with patch.object(web, 'ModeratePages', self.fast_navigator), self.assertRaisesRegex(capture.CaptureError, 'stopped_by_user'):
            collector.run()
        collector.bundle()
        self.assertEqual(app.snapshot()['saved'], 3)
        self.assertEqual(len(app.snapshot()['summary_rows']), 3)
        self.assertFalse(collector.manifest['student_pagination_completed'])
        self.assertTrue(collector.manifest['moderate_pages'][0]['captured'])
        self.assertFalse(collector.manifest['moderate_pages'][1]['captured'])
        with zipfile.ZipFile(collector.run_out / 'coded-logs.zip') as archive:
            self.assertEqual(sum(n.startswith('log_') for n in archive.namelist()), 3)


if __name__ == '__main__':
    unittest.main()
