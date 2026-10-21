"""Cancellation/concurrency unit tests; no application config, database or network."""
from pathlib import Path
import importlib.util
import threading
import unittest

spec = importlib.util.spec_from_file_location('isolated_generation_runtime', Path(__file__).resolve().parents[1] / 'app/services/generation_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class GenerationRuntimeTests(unittest.TestCase):
    def test_cancel_closes_exact_transport_once_and_cannot_resume(self):
        calls = []
        control = runtime.GenerationControl()
        callback = lambda: calls.append('closed')
        control.set_abort(callback)
        control.cancel(); control.cancel()
        self.assertEqual(calls, ['closed'])
        with self.assertRaises(runtime.GenerationCancelled): control.checkpoint()
        with self.assertRaises(runtime.GenerationCancelled): control.progress('generating')

    def test_registering_after_cancellation_closes_new_transport_without_egress(self):
        calls = []
        control = runtime.GenerationControl(); control.cancel()
        with self.assertRaises(runtime.GenerationCancelled): control.set_abort(lambda: calls.append('closed'))
        self.assertEqual(calls, ['closed'])

    def test_clearing_an_old_transport_cannot_unhook_a_new_one(self):
        calls = []
        control = runtime.GenerationControl()
        first = lambda: calls.append(1)
        second = lambda: calls.append(2)
        control.set_abort(first); control.set_abort(second); control.clear_abort(first)
        control.cancel()
        self.assertEqual(calls, [2])

    def test_cleared_transport_and_close_errors_do_not_trigger_retries(self):
        control = runtime.GenerationControl()
        callback = lambda: (_ for _ in ()).throw(RuntimeError('SYNTHETIC_PRIVATE_TRANSPORT_ERROR'))
        control.set_abort(callback); control.clear_abort(callback); control.cancel()
        control = runtime.GenerationControl(); control.set_abort(callback); control.cancel()
        with self.assertRaises(runtime.GenerationCancelled): control.checkpoint()

    def test_status_is_a_small_whitelist_not_prompt_or_reasoning_data(self):
        events = []
        control = runtime.GenerationControl(events.append)
        control.progress('generating', received_chars=120000, text='PRIVATE-PROMPT', reasoning='PRIVATE-REASONING')
        self.assertEqual(events, [{'event': 'status', 'stage': 'generating', 'received_chars': 120000}])
        control.progress('unrecognized', received_chars=4)
        self.assertEqual(len(events), 1)
        heartbeat = control.heartbeat()
        self.assertEqual(set(heartbeat), {'event', 'stage', 'received_chars', 'elapsed_seconds'})
        self.assertEqual(heartbeat['received_chars'], 120000)
        self.assertNotIn('PRIVATE', str(heartbeat))

    def test_generation_slots_are_per_user_and_globally_bounded_not_text_limited(self):
        slots = runtime.GenerationSlots(maximum=2)
        first = slots.acquire(1)
        with self.assertRaises(runtime.GenerationBusy): slots.acquire(1)
        second = slots.acquire(2)
        with self.assertRaises(runtime.GenerationBusy): slots.acquire(3)
        first(); first()
        third = slots.acquire(3)
        second(); third()
        self.assertEqual(slots._owners, set())

    def test_cancel_before_final_commit_guard_prevents_work(self):
        control = runtime.GenerationControl(); control.cancel()
        with self.assertRaises(runtime.GenerationCancelled):
            with control.finalizing(): self.fail('Must not enter cancelled commit')

    def test_final_commit_guard_finishes_before_a_racing_cancel(self):
        control = runtime.GenerationControl()
        attempting = threading.Event(); done = threading.Event()
        def stop():
            attempting.set(); control.cancel(); done.set()
        with control.finalizing():
            thread = threading.Thread(target=stop); thread.start()
            self.assertTrue(attempting.wait(1))
            self.assertFalse(done.is_set())
        thread.join(1)
        self.assertTrue(done.is_set())
        with self.assertRaises(runtime.GenerationCancelled): control.checkpoint()


if __name__ == '__main__': unittest.main()