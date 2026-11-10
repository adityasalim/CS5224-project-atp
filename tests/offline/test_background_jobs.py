"""No model/AWS calls: verify work outlives the response and is claimed once."""
import importlib.util
import pathlib
import threading
import types
import unittest
from test_request_recovery import RecoveryTests, definitions, ROOT

spec = importlib.util.spec_from_file_location('background_jobs', ROOT / 'agent/background_jobs.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
BackgroundJobs, SessionBusy = module.BackgroundJobs, module.SessionBusy


class App:
    def __init__(self):
        self.started, self.finished = [], []
        self.done = threading.Event()
    def add_async_task(self, *args):
        self.started.append(args); return 'task'
    def complete_async_task(self, task):
        self.finished.append(task); self.done.set()


class BackgroundTests(RecoveryTests):
    def background_invoke(self, planning, runner):
        # Reuse the real entrypoint/processing code under deterministic dependencies.
        invoke, context = self.invoke(planning)
        ns = invoke.__globals__
        ns.update(_background=runner, SessionBusy=SessionBusy)
        return invoke, context

    def test_close_response_does_not_cancel_work_or_lose_pdf_pointer(self):
        app = App(); runner = BackgroundJobs(app)
        release, entered = threading.Event(), threading.Event()
        calls = []
        def planning(*args):
            calls.append(args); entered.set()
            if not release.wait(3): raise RuntimeError('test timed out')
            yield {'type': 'itinerary', 'itinerary': {'tripId': 'saved', 'version': 1, 'historySaved': True}}
        invoke, context = self.background_invoke(planning, runner)
        payload = {'prompt': 'Plan', 'requestId': 'request-1', 'background': True}
        response = invoke(payload, context)
        receipt = next(response)
        self.assertTrue(receipt['background']); response.close()
        self.assertTrue(entered.wait(1))
        replay = list(invoke(payload, context))
        self.assertTrue(replay[0]['duplicate']); self.assertEqual(len(calls), 1)
        rejected = list(invoke(dict(payload, requestId='request-2'), context))
        self.assertEqual(rejected[0]['code'], 'SESSION_BUSY')
        self.assertNotIn(('alice', 'run_request-2'), self.trips.rows)
        release.set(); self.assertTrue(app.done.wait(2))
        row = self.trips.rows[('alice', 'run_request-1')]
        self.assertEqual(row['status'], 'completed'); self.assertEqual(row['resultTripId'], 'saved')
        self.assertEqual(len(app.started), 1); self.assertEqual(app.finished, ['task'])

    def test_worker_failure_is_saved_and_task_registration_ends(self):
        app = App(); runner = BackgroundJobs(app)
        def fail(*args):
            yield {'type': 'status', 'message': 'Working'}
            raise RuntimeError('provider failed')
        invoke, context = self.background_invoke(fail, runner)
        list(invoke({'prompt': 'Plan', 'requestId': 'request-1', 'background': True}, context))
        self.assertTrue(app.done.wait(2))
        self.assertEqual(self.trips.rows[('alice', 'run_request-1')]['status'], 'failed')

    def test_background_question_keeps_choices(self):
        app = App(); runner = BackgroundJobs(app)
        def ask(*args):
            yield {'type': 'question', 'message': 'Any preferences?', 'choices': ['Use defaults']}
        invoke, context = self.background_invoke(ask, runner)
        list(invoke({'prompt': 'Plan', 'requestId': 'request-1', 'background': True}, context))
        self.assertTrue(app.done.wait(2))
        row = self.trips.rows[('alice', 'run_request-1')]
        self.assertEqual(row['status'], 'completed'); self.assertEqual(row['choices'], ['Use defaults'])

    def test_missing_sdk_does_not_claim_or_start_a_job(self):
        runner = BackgroundJobs(object()); claims = []
        with self.assertRaises(RuntimeError):
            runner.submit(('alice', 'chat'), 'one', lambda: claims.append(True), lambda: None)
        self.assertEqual(claims, [])

    def test_thread_start_failure_releases_sdk_registration(self):
        app = App()
        class BrokenThread:
            def __init__(self, **kwargs): pass
            def start(self): raise RuntimeError('cannot start')
        runner = BackgroundJobs(app, BrokenThread)
        invoke, context = self.background_invoke(lambda *args: iter(()), runner)
        events = list(invoke({'prompt': 'Plan', 'requestId': 'request-1', 'background': True}, context))
        self.assertEqual(events[-1]['code'], 'HISTORY_UNAVAILABLE')
        self.assertEqual(self.trips.rows[('alice', 'run_request-1')]['status'], 'failed')
        self.assertEqual(app.finished, ['task'])
        self.assertIsNone(runner.busy(('alice', 'session-1')))


if __name__ == '__main__':
    unittest.main()
