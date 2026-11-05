"""Local browser tests with synthetic HTML and blocked page network requests."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quick_capture as app

FIXTURE = '''<!doctype html><html><body><main></main><script>
window.reverseList=false;
function draw(){
 const n=Number(location.hash.replace('#log-',''));
 if(n){document.querySelector('main').innerHTML=`<h1>Attempt activity details</h1><p>Synthetic student ${n}</p><p>Completed attempt 1</p><p>00:00 Started</p><p>00:10 Synthetic event ${n}</p>`;}
 else{let ids=window.reverseList?[3,2,1]:[1,2,3];document.querySelector('main').innerHTML='<h1>Moderate synthetic quiz</h1><table>'+ids.map(n=>`<tr><td>Synthetic student ${n}</td><td><a href="#log-${n}" onclick="event.preventDefault();history.pushState({},'',this.href);draw()">View Log</a></td></tr>`).join('')+'</table>';}
}
window.addEventListener('popstate',draw);draw();
</script></body></html>'''


@unittest.skipUnless(os.environ.get("CANVAS_RUN_BROWSER_TESTS") == "1", "optional local browser test")
class QuickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True, channel=os.environ.get("CANVAS_TEST_BROWSER", "msedge" if sys.platform == "win32" else "chrome"))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.context.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=FIXTURE)
                        if route.request.url.split('#')[0] == 'http://canvas-quick-fixture.invalid/fixture' else route.abort())
        self.page.goto('http://canvas-quick-fixture.invalid/fixture')
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.context.close()
        self.temp.cleanup()

    def collector(self, responses=("start", "Attempt activity details", "checked")):
        answers = iter(responses)
        return app.QuickCapture(self.page, Path(self.temp.name)/"captures", prompt=lambda _: next(answers), settle=0)

    def test_all_three_logs_and_local_bundle(self):
        collector = self.collector()
        collector.run()
        collector.bundle()
        self.assertEqual(len(collector.manifest['saved_logs']),3)
        self.assertTrue(collector.manifest['page_loop_completed'])
        self.assertFalse(collector.manifest['all_events_verified'])
        self.assertFalse(collector.manifest['all_students_verified'])
        self.assertTrue((collector.out/'captured-logs.zip').exists())
        for i in range(1,4):
            entry=collector.manifest['saved_logs'][i-1]
            self.assertEqual(entry['student_name'],f'Synthetic student {i}')
            self.assertIn(f'Student: Synthetic student {i}',(collector.out/entry['file']).read_text())
            self.assertEqual(entry['sha256'],app.hashlib.sha256((collector.out/entry['file']).read_bytes()).hexdigest())
        self.assertTrue(collector.manifest['names_locally_reviewed'])
        self.assertEqual(len(app.controls_on_page(self.page)[0]),3)

    def test_iframe_list_and_same_tab_history(self):
        self.page.set_content('<h1>Outer page</h1><iframe style="width:900px;height:700px" src="http://canvas-quick-fixture.invalid/fixture"></iframe>')
        collector = self.collector()
        collector.run()
        self.assertEqual(len(collector.manifest['saved_logs']),3)
        self.assertIn('Synthetic student 3',(collector.out/collector.manifest['saved_logs'][2]['file']).read_text())

    def test_marker_already_on_moderate_rejected(self):
        collector = self.collector(("start","Moderate synthetic quiz"))
        with self.assertRaisesRegex(app.CaptureError,'readiness_phrase_is_empty_or_already_on_moderate'):
            collector.run()
        self.assertFalse(collector.manifest['page_loop_completed'])

    def test_stop_after_first_preserves_capture(self):
        collector = self.collector(("start","Attempt activity details",""))
        with self.assertRaisesRegex(app.CaptureError,'first_capture_not_confirmed'):
            collector.run()
        self.assertEqual(len(collector.manifest['saved_logs']),1)
        self.assertEqual(json.loads((collector.out/'manifest.json').read_text())['error'],'first_capture_not_confirmed')

    def test_reordered_list_stops(self):
        answers = iter(("start","Attempt activity details","checked"))
        def answer(_):
            result=next(answers)
            if result=='checked':self.page.evaluate('window.reverseList=true')
            return result
        collector = app.QuickCapture(self.page,Path(self.temp.name)/'captures',prompt=answer,settle=0)
        original = app.wait_list
        with patch.object(app,'wait_list',lambda page,sig,**kwargs:original(page,sig,timeout=.5,**kwargs)):
            with self.assertRaisesRegex(app.CaptureError,'back_did_not_restore_the_same_moderate_page'):
                collector.run()
        self.assertEqual(len(collector.manifest['saved_logs']),1)

    def test_submit_control_is_never_selected(self):
        self.page.set_content('<button onclick="throw Error(\'must not click\')">Submit This Attempt</button>')
        with self.assertRaisesRegex(app.CaptureError,'no_view_log_controls_found'):
            app.controls_on_page(self.page)

    def test_visible_text_matches_with_personalized_aria_label(self):
        self.page.locator('a').first.evaluate("e=>e.setAttribute('aria-label','View log for Synthetic private name')")
        controls, _ = app.controls_on_page(self.page)
        self.assertEqual(len(controls),3)

    def test_diagnostic_does_not_include_page_text_or_urls(self):
        self.page.locator('a').first.evaluate("e=>e.setAttribute('aria-label','View log for Sensitive placeholder')")
        report = app.diagnostic_report(self.page.context)
        text = json.dumps(report)
        self.assertNotIn('Sensitive',text)
        self.assertNotIn('Synthetic',text)
        self.assertNotIn('http',text)
        self.assertEqual(report['tabs'][0]['frames'][0]['either_exact'],3)

    def test_find_moderate_in_another_tab(self):
        second = self.page.context.new_page()
        second.set_content('<p>Unrelated synthetic tab</p>')
        self.assertIs(app.choose_moderate_page(self.page.context),self.page)

    def test_multiple_tabs_require_choice(self):
        second = self.page.context.new_page()
        second.set_content('<button>View Log</button>')
        with self.assertRaisesRegex(app.CaptureError,'moderate_tab_not_selected'):
            app.choose_moderate_page(self.page.context,prompt=lambda _:'')
        self.assertIs(app.choose_moderate_page(self.page.context,prompt=lambda _:'2'),second)

    def test_nonstandard_text_diagnostic(self):
        self.page.set_content('<div onclick="void(0)">View Log</div><button disabled>View Log</button>')
        frame=app.diagnostic_report(self.page.context)['tabs'][0]['frames'][0]
        self.assertEqual(frame['exact_text_outside_standard_control'],1)
        self.assertEqual(frame['disabled_exact'],1)
        self.assertEqual(frame['either_exact'],0)

    def test_script_failure_fallback_redacts_error(self):
        secret='Private example name https://private.invalid/path?token=secret'
        with patch.object(self.page.main_frame,'evaluate',side_effect=RuntimeError('TypeError: '+secret)):
            result=app.diagnostic_report(self.context)
        frame=result['tabs'][0]['frames'][0]
        self.assertEqual(frame['inspection_method'],'locator_fallback')
        self.assertEqual(frame['script_error_category'],'page_script_type_error')
        self.assertEqual(frame['locator_probe']['visible_exact_text_matches'],3)
        self.assertNotIn('inspection_failed',frame)
        self.assertNotIn('Private',json.dumps(result))
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('http',json.dumps(result))

    def test_svg_link_does_not_abort_matching(self):
        self.page.locator('body').evaluate("e=>e.insertAdjacentHTML('afterbegin','<svg width=100 height=40><a href=\"#\"><text x=0 y=20>Navigation icon</text></a></svg>')")
        controls,_=app.controls_on_page(self.page)
        self.assertEqual(len(controls),3)

    def test_error_categories_never_echo_message(self):
        for message,expected in [('EvalError: TrustedScript required private text','page_script_security_restriction'),
                                 ('Execution context was destroyed private text','page_navigated_during_inspection'),
                                 ('completely unknown private text','browser_inspection_error')]:
            self.assertEqual(app.safe_error_category(RuntimeError(message)),expected)

    def test_45_extended_accessible_button_names(self):
        self.page.set_content(''.join(f'<button aria-label="View Log for synthetic {n}">Log</button>' for n in range(45)))
        controls,signature=app.controls_on_page(self.page)
        self.assertEqual(len(controls),45)
        self.assertEqual(len(set(signature)),45)

    def test_extended_label_full_capture(self):
        variant=FIXTURE.replace('>View Log</a>',' aria-label="View Log for synthetic ${n}">Log</a>')
        self.page.set_content(variant)
        collector=self.collector()
        collector.run()
        self.assertEqual(len(collector.manifest['saved_logs']),3)
        self.assertTrue(collector.manifest['page_loop_completed'])

    def test_accessible_name_from_labelledby(self):
        self.page.set_content('<span id="label1">View Log for synthetic student</span><button aria-labelledby="label1">Log</button>')
        self.assertEqual(len(app.controls_on_page(self.page)[0]),1)

    def test_waits_for_list_after_empty_document(self):
        self.page.set_content('<main></main>')
        self.page.evaluate("setTimeout(()=>document.querySelector('main').innerHTML='<button>View Log for synthetic student</button>',300)")
        chosen=app.choose_moderate_page(self.context,timeout=5,stable_seconds=.2)
        self.assertIs(chosen,self.page)

    def test_changing_generated_id_does_not_change_identity(self):
        self.page.locator('a').first.evaluate("e=>e.id='generated-1'")
        before=app.controls_on_page(self.page)[1]
        self.page.locator('a').first.evaluate("e=>e.id='generated-2'")
        self.assertEqual(before,app.controls_on_page(self.page)[1])

    def test_submit_is_not_matched_by_prefix(self):
        self.page.set_content('<button>Submit This Attempt</button><button aria-label="View Log for synthetic student">Log</button>')
        self.assertEqual(len(app.controls_on_page(self.page)[0]),1)

    def test_name_added_when_log_page_has_none(self):
        self.page.set_content(FIXTURE.replace('<p>Synthetic student ${n}</p>',''))
        collector=self.collector()
        collector.run()
        entry=collector.manifest['saved_logs'][0]
        self.assertEqual(entry['student_name'],'Synthetic student 1')
        self.assertTrue(entry['file'].startswith('log_001_Synthetic_student_1'))
        text=(collector.out/entry['file']).read_text()
        self.assertTrue(text.startswith('Student: Synthetic student 1\n'))

    def test_name_from_accessible_label_and_labelledby(self):
        self.page.set_content('<span id="name">View Log for Synthetic Example</span><button aria-labelledby="name">Log</button>')
        control=app.controls_on_page(self.page)[0][0]
        self.assertEqual(app.proposed_student_name(control),('Synthetic Example','view_log_accessible_name'))

    def test_manual_name_when_automatic_name_is_unavailable(self):
        self.page.set_content('<button>View Log</button>')
        controls,signature=app.controls_on_page(self.page)
        collector=self.collector()
        collector.prepare_names(controls)
        with self.assertRaisesRegex(app.CaptureError,'required_student_name_missing_or_too_long'):
            collector.load_reviewed_names()
        with (collector.out/'names.csv').open('w',encoding='utf-8-sig',newline='') as handle:
            writer=app.csv.DictWriter(handle,fieldnames=('control_position','student_name','proposed_source'))
            writer.writeheader();writer.writerow({'control_position':1,'student_name':'Renée Synthetic','proposed_source':'manual_required'})
        collector.load_reviewed_names()
        collector.save(0,'Nameless log',signature)
        self.assertEqual(collector.manifest['saved_logs'][0]['student_name'],'Renée Synthetic')

    def test_named_copy_matches_hash_not_old_position(self):
        controls,signature=app.controls_on_page(self.page)
        old=Path(self.temp.name)/'old';old.mkdir()
        text='Nameless old log\n00:10 Synthetic event\n'
        (old/'log_001.txt').write_text(text,encoding='utf-8',newline='\r\n')
        prior={'format':'canvas_quick_capture_v1','saved_logs':[{'file':'log_001.txt','sha256':app.digest(text),
                'list_control_sha256':signature[2],'control_position':1}]}
        (old/'manifest.json').write_text(json.dumps(prior),encoding='utf-8')
        original=(old/'log_001.txt').read_bytes()
        collector=self.collector(('label',))
        collector.label_existing(old)
        entry=collector.manifest['saved_logs'][0]
        self.assertEqual(entry['student_name'],'Synthetic student 3')
        self.assertIn('Nameless old log',(collector.out/entry['file']).read_text())
        self.assertEqual((old/'log_001.txt').read_bytes(),original)
        self.assertTrue(collector.manifest['existing_files_labelled'])
        with (collector.out/'names.csv').open(encoding='utf-8-sig',newline='') as handle:
            self.assertEqual(len(list(app.csv.DictReader(handle))),1)

    def test_existing_file_unmatched_is_not_labelled(self):
        old=Path(self.temp.name)/'old';old.mkdir()
        prior={'format':'canvas_quick_capture_v1','saved_logs':[{'file':'log_001.txt','sha256':'missing','list_control_sha256':'not_a_match'}]}
        (old/'manifest.json').write_text(json.dumps(prior),encoding='utf-8')
        collector=self.collector(('label',))
        with self.assertRaisesRegex(app.CaptureError,'old_capture_not_uniquely_matched_to_moderate'):
            collector.label_existing(old)
        self.assertFalse(collector.manifest['saved_logs'])

    def test_same_text_for_different_students_is_flagged(self):
        controls,signature=app.controls_on_page(self.page)
        collector=self.collector()
        collector.prepare_names(controls);collector.load_reviewed_names()
        collector.save(0,'Same nameless rendered text',signature)
        collector.save(1,'Same nameless rendered text',signature)
        self.assertTrue(collector.manifest['saved_logs'][1]['same_page_text_as_another_control'])
        self.assertNotEqual(collector.manifest['saved_logs'][0]['file'],collector.manifest['saved_logs'][1]['file'])
        with self.assertRaisesRegex(app.CaptureError,'duplicate_log_text_needs_local_review'):
            collector.save(1,'Same nameless rendered text',signature)

    def test_names_csv_formula_protection_and_safe_filename(self):
        self.assertTrue(app.csv_name('=Synthetic Name').startswith("'="))
        value=app.filename_name('../../Renée: Example\\name?\n')
        self.assertNotIn('/',value);self.assertNotIn('\\',value);self.assertNotIn(':',value)
        self.assertNotIn('?',value);self.assertNotIn('\n',value)

    def test_existing_file_path_traversal_is_rejected(self):
        signature=app.controls_on_page(self.page)[1]
        old=Path(self.temp.name)/'old';old.mkdir()
        outside=Path(self.temp.name)/'outside.txt';outside.write_text('Synthetic outside file')
        prior={'format':'canvas_quick_capture_v1','saved_logs':[{'file':'../outside.txt','sha256':app.digest('Synthetic outside file'),
                'list_control_sha256':signature[0]}]}
        (old/'manifest.json').write_text(json.dumps(prior))
        collector=self.collector(('label',))
        with self.assertRaisesRegex(app.CaptureError,'source_file_outside_export_or_missing'):
            collector.label_existing(old)
        self.assertFalse(collector.manifest['saved_logs'])

    def test_changed_existing_file_is_not_labelled(self):
        signature=app.controls_on_page(self.page)[1]
        old=Path(self.temp.name)/'old';old.mkdir()
        (old/'log_001.txt').write_text('Changed synthetic text')
        prior={'format':'canvas_quick_capture_v1','saved_logs':[{'file':'log_001.txt','sha256':app.digest('Original text'),
                'list_control_sha256':signature[0]}]}
        (old/'manifest.json').write_text(json.dumps(prior))
        collector=self.collector(('label',))
        with self.assertRaisesRegex(app.CaptureError,'old_capture_integrity_check_failed'):
            collector.label_existing(old)
        self.assertFalse(collector.manifest['saved_logs'])

    def test_name_not_taken_from_unrelated_nested_table(self):
        self.page.set_content('<ul><li><div><table><tr><td>Other Synthetic Name</td></tr></table></div>'
                              '<button aria-label="View Log for Correct Synthetic Name">Log</button></li></ul>')
        control=app.controls_on_page(self.page)[0][0]
        self.assertEqual(app.proposed_student_name(control)[0],'Correct Synthetic Name')


if __name__ == '__main__':
    unittest.main()
