"""Real signed CPI-stub runs retain admitted images and report their hashes.

The signer forwarder remains an explicit synthetic CPI driver; these tests do
not claim it is a captured caller or that the candidate is reconstructed.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/pump_set_mayhem_virtual_params_differential.py'
ORIGINAL = ROOT / 'evidence/original/pump/deployed.so'
OTHER = ROOT / 'evidence/original/pump_fees/deployed.so'
INPUTS = {'original.so': ORIGINAL, 'candidate.so': ORIGINAL,
          'stub.so': ROOT / 'scripts/fixtures/signer_stub.so',
          'token22.so': ROOT / 'evidence/dependencies/spl_token_2022/deployed.so',
          'ata.so': ROOT / 'evidence/dependencies/associated_token/deployed.so'}
DRIVER = r'''
import sys, shutil
from pathlib import Path
scripts, directory, replacement, mode = sys.argv[1:]
sys.path.insert(0, scripts)
import pump_set_mayhem_virtual_params_differential as module
root = Path(directory)
module.ORIGINAL = root / 'original.so'
module.STUB = root / 'stub.so'
module.DEPENDENCIES = {module.TOKEN22: root / 'token22.so', module.ATA: root / 'ata.so'}
returned = []
def hook(frame, event, arg):
    if event == 'return' and frame.f_code.co_name == 'run' and frame.f_code.co_filename == module.__file__:
        returned.append(1)
        if len(returned) == 2:
            sys.setprofile(None)
            for path in root.glob('*.so'):
                shutil.copyfile(replacement, path)
if mode == 'mutate':
    sys.setprofile(hook)
sys.argv = [module.__file__, '--output', str(root / 'report.json.gz')]
if mode != 'original-only':
    sys.argv += ['--candidate', str(root / 'candidate.so')]
try:
    module.main()
finally:
    sys.setprofile(None)
    if mode == 'mutate' and len(returned) < 2:
        raise RuntimeError('first original/candidate pair did not execute')
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SetMayhemVirtualParamsPinnedImageTests(unittest.TestCase):
    def staged_run(self, mode):
        temporary = tempfile.TemporaryDirectory(prefix='set-mayhem-binding-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        for name, path in INPUTS.items():
            shutil.copyfile(path, directory / name)
        if mode == 'wrong':
            shutil.copyfile(OTHER, directory / 'candidate.so')
        elif mode == 'wrong-original':
            shutil.copyfile(OTHER, directory / 'original.so')
        environment = dict(os.environ)
        environment.pop('PYTHONOPTIMIZE', None)
        result = subprocess.run([sys.executable, '-B', '-c', DRIVER, str(ROOT / 'scripts'),
                                 str(directory), str(OTHER), mode],
                                cwd=ROOT, env=environment, text=True, capture_output=True, timeout=120)
        return result, directory

    def test_all_images_keep_admitted_bytes_after_replacement(self):
        result, directory = self.staged_run('mutate')
        self.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        for name, original in INPUTS.items():
            self.assertNotEqual(sha(directory / name), sha(original), name)
        report = json.loads(gzip.decompress((directory / 'report.json.gz').read_bytes()))
        for field, name in (('original_sha256', 'original.so'), ('candidate_sha256', 'candidate.so'), ('stub_sha256', 'stub.so')):
            self.assertEqual(report[field], sha(INPUTS[name]))
        self.assertEqual(report['mismatches'], [])
        self.assertGreater(len(report['cases']), 1)
        self.assertTrue(all(not row['diff'] for row in report['cases']))

    def test_wrong_real_candidate_reports_mismatches(self):
        result, directory = self.staged_run('wrong')
        self.assertEqual(result.returncode, 1, result.stderr[-2000:])
        report = json.loads(gzip.decompress((directory / 'report.json.gz').read_bytes()))
        self.assertEqual(report['candidate_sha256'], sha(OTHER))
        self.assertTrue(report['mismatches'])

    def test_wrong_original_is_rejected_before_publication(self):
        result, directory = self.staged_run('wrong-original')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('AssertionError', result.stderr)
        self.assertFalse((directory / 'report.json.gz').exists())

    def test_original_only_remains_supported(self):
        result, directory = self.staged_run('original-only')
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(gzip.decompress((directory / 'report.json.gz').read_bytes()))
        self.assertIsNone(report['candidate_sha256'])
        self.assertEqual(report['mismatches'], [])
        self.assertTrue(all(row['candidate'] is None for row in report['cases']))

    def test_optimized_refusal_and_normal_cli(self):
        environment = dict(os.environ)
        environment.pop('PYTHONOPTIMIZE', None)
        for flags, env in ((['-O'], environment), (['-OO'], environment),
                           ([], dict(environment, PYTHONOPTIMIZE='1'))):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, '-B', *flags, str(SCRIPT), '--help'],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 1)
                self.assertIn('Normal Python is required', result.stderr)
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--help'], cwd=ROOT,
                                env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
