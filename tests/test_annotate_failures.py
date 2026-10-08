import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'annotate_failures', Path(__file__).resolve().parents[1] / 'packaging' / 'annotate_failures.py')
annotate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(annotate)

OUTPUT = '''test_a (test_x.T.test_a) ... ok
test_b (test_x.T.test_b) ... ERROR

======================================================================
ERROR: test_b (test_x.T.test_b)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "x.py", line 3, in test_b
OSError: 无法保存全屏密码

======================================================================
FAIL: test_c (test_x.T.test_c)
----------------------------------------------------------------------
AssertionError: 1 != 2, really

----------------------------------------------------------------------
Ran 3 tests in 0.1s

FAILED (failures=1, errors=1)
'''


class AnnotateFailuresTests(unittest.TestCase):
    def test_each_failure_becomes_one_annotation_with_its_traceback(self):
        lines = annotate.annotations(OUTPUT)
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith('::error title=ERROR%3A test_b (test_x.T.test_b)::Traceback'))
        self.assertIn('OSError: 无法保存全屏密码', lines[0])
        self.assertNotIn('\n', lines[0])
        self.assertIn('AssertionError%3A', annotate.escape('AssertionError:', True))
        self.assertIn('FAIL%3A test_c', lines[1])
        self.assertNotIn('Ran 3 tests', lines[1])

    def test_a_long_traceback_keeps_its_end(self):
        body = 'x\n' * 5000 + 'TheRealError'
        text = '=' * 70 + '\nERROR: t\n' + '-' * 70 + '\n' + body + '\n'
        self.assertTrue(annotate.annotations(text)[0].endswith('TheRealError'))


if __name__ == '__main__':
    unittest.main()
