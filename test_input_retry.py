"""Verify definite pre-input rejection retry; uncertainty must never replay."""
import unittest
from unittest.mock import MagicMock, patch
import cube_assist as c


REJECTED = {'ok': False, 'error': 'The current native game frame is not acknowledged by the encoder'}
SUCCESS = {'ok': True, 'request_id': 'test', 'round_trip_seconds': .1,
           'reason': 'requested_ticks_finished_recorded_frozen',
           'state': {'p1': {'dead': False}}}


class RetrySafety(unittest.TestCase):
    def invoke(self, side_effect):
        with patch.object(c.m, 'client', return_value={'ok': True}), \
             patch.object(c.b, 'request', side_effect=side_effect) as request, \
             patch.object(c.r, 'ROOT', MagicMock()), patch.object(c.time, 'sleep'):
            try:
                c.execute(1, False, 'test')
            except RuntimeError:
                return request.call_count, False
            return request.call_count, True

    def test_definite_pre_input_rejection_may_retry(self):
        self.assertEqual(self.invoke([REJECTED, SUCCESS]), (2, True))

    def test_retries_are_bounded(self):
        self.assertEqual(self.invoke([REJECTED] * 5), (5, False))

    def test_uncertain_input_is_never_replayed(self):
        self.assertEqual(self.invoke(RuntimeError('Uncertain request')), (1, False))

    def test_other_rejections_are_not_replayed(self):
        self.assertEqual(self.invoke([{'ok': False, 'error': 'Recording lease expired'}]), (1, False))


if __name__ == '__main__':
    unittest.main()
