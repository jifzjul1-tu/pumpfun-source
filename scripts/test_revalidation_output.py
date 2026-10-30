"""Exercise fresh audit output admission through the real revalidation CLI."""
import hashlib
import errno
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock


REPO = Path(__file__).resolve().parents[1]


class StandalonePublicationTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'revalidation_publication_under_test', REPO / 'scripts/revalidate_all.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def fallback_operations(self):
        with mock.patch.dict(sys.modules, {'deep_disassembly': None}):
            return self.module.publication_operations()

    def test_shared_publisher_is_preferred(self):
        rename, temporary = object(), object()
        shared = SimpleNamespace(rename_directory=rename, temporary_sibling=temporary)
        with mock.patch.dict(sys.modules, {'deep_disassembly': shared}):
            self.assertEqual(self.module.publication_operations(), (rename, temporary))

    def test_unrelated_import_failure_does_not_enable_fallback(self):
        import builtins
        original = builtins.__import__

        def fail(name, *args, **kwargs):
            if name == 'deep_disassembly':
                raise ModuleNotFoundError('broken helper dependency', name='other_dependency')
            return original(name, *args, **kwargs)

        with mock.patch('builtins.__import__', fail):
            with self.assertRaises(ModuleNotFoundError) as caught:
                self.module.publication_operations()
        self.assertEqual(caught.exception.name, 'other_dependency')

    def test_fallback_preserves_late_destinations(self):
        rename, temporary = self.fallback_operations()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for occupied in (False, True):
                destination = root / str(occupied)
                staging = temporary(destination, 'staging')
                (staging / 'new').write_bytes(b'new')
                destination.mkdir()
                if occupied:
                    (destination / 'old').write_bytes(b'old')
                with self.assertRaises(OSError) as caught:
                    rename(staging, destination)
                self.assertEqual(caught.exception.errno, errno.EEXIST)
                self.assertEqual(sorted(p.name for p in destination.iterdir()),
                                 ['old'] if occupied else [])
                self.assertEqual((staging / 'new').read_bytes(), b'new')

    @unittest.skipIf(os.name == 'nt', 'POSIX umask semantics')
    def test_fallback_publication_keeps_normal_directory_mode(self):
        rename, temporary = self.fallback_operations()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'result'
            previous = os.umask(0o027)
            try:
                staging = temporary(destination, 'staging')
                (staging / 'data').write_bytes(b'complete')
                rename(staging, destination)
            finally:
                os.umask(previous)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o750)
            self.assertEqual((destination / 'data').read_bytes(), b'complete')

    @unittest.skipIf(os.name == 'nt', 'POSIX native API selection')
    def test_unsupported_platform_fails_before_publication(self):
        rename, temporary = self.fallback_operations()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'result'
            staging = temporary(destination, 'staging')
            with mock.patch.object(sys, 'platform', 'unsupported-os'):
                with self.assertRaises(OSError) as caught:
                    rename(staging, destination)
            self.assertEqual(caught.exception.errno, errno.ENOTSUP)
            self.assertFalse(destination.exists())
            self.assertTrue(staging.is_dir())


class RevalidationOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'scripts').mkdir()
        shutil.copyfile(REPO / 'scripts/revalidate_all.py',
                        self.root / 'scripts/revalidate_all.py')
        self.packet = self.root / 'research/retained-idl'
        self.packet.mkdir(parents=True)
        self.source = (REPO / 'evidence/upstream/pump.json').read_bytes()
        (self.packet / 'pump.json').write_bytes(self.source)
        (self.packet / 'manifest.json').write_text(json.dumps({
            'pump.json': hashlib.sha256(self.source).hexdigest(),
        }), encoding='utf-8')

    def run_audit(self, output):
        return subprocess.run(
            [sys.executable, '-B', str(self.root / 'scripts/revalidate_all.py'),
             '--output', str(output)],
            cwd=self.root, capture_output=True, encoding='utf-8',
        )

    def test_output_under_research_is_not_counted_as_an_input_packet(self):
        output = self.root / 'research/new-audit'
        result = self.run_audit(output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = json.loads((output / 'results.json').read_text(encoding='utf-8'))
        self.assertEqual([row['packet'] for row in rows], ['retained-idl'])
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual((summary['total_packets'], summary['pass'],
                          summary['skip_or_inconclusive']), (1, 1, 0))

    def test_existing_output_is_preserved_before_any_verifier_runs(self):
        output = self.root / 'prior-audit'
        output.mkdir()
        prior = {'results.json': b'previous sealed results\n',
                 'README.md': b'previous report\n'}
        for name, raw in prior.items():
            (output / name).write_bytes(raw)
        marker = self.root / 'verifier-ran'
        (self.packet / 'verify.py').write_text(
            'from pathlib import Path\n'
            f'Path({str(marker)!r}).write_text("ran", encoding="utf-8")\n',
            encoding='utf-8',
        )
        result = self.run_audit(output)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), 'output admission must precede packet execution')
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, prior)
        self.assertEqual((self.packet / 'pump.json').read_bytes(), self.source)

    def test_fresh_external_report_remains_self_verifiable(self):
        output = self.root / 'reports/audit'
        result = self.run_audit(output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verified = subprocess.run([sys.executable, '-B', str(output / 'verify.py')],
                                  capture_output=True, encoding='utf-8')
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual((self.packet / 'pump.json').read_bytes(), self.source)

    def test_manifest_paths_cannot_escape_the_packet(self):
        outside = self.packet.parent / 'outside.txt'
        outside.write_bytes(b'outside\n')
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        cases = {
            'parent': '../outside.txt',
            'absolute': str(outside),
            'backslash': r'..\outside.txt',
        }
        for name, manifest_path in cases.items():
            with self.subTest(name=name):
                (self.packet / 'manifest.json').write_text(json.dumps({
                    manifest_path: digest,
                }), encoding='utf-8')
                output = self.root / ('audit-' + name)
                result = self.run_audit(output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                row = json.loads((output / 'results.json').read_text(encoding='utf-8'))[0]
                self.assertEqual(row['manifest_check']['status'], 'fail')
                self.assertEqual(row['manifest_check']['errors'], ['unsafe_path: ' + manifest_path])
                self.assertEqual(row['overall'], 'fail')

    def test_manifest_files_and_ancestors_cannot_be_links(self):
        outside_file = self.root / 'outside.txt'
        outside_file.write_bytes(b'outside\n')
        outside_dir = self.root / 'outside'
        outside_dir.mkdir()
        (outside_dir / 'nested.txt').write_bytes(b'nested\n')
        try:
            os.symlink(outside_file, self.packet / 'alias.txt')
            os.symlink(outside_dir, self.packet / 'nested', target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as error:
            self.skipTest(f'creating links is not permitted: {error}')
        (self.packet / 'manifest.json').write_text(json.dumps({
            'alias.txt': hashlib.sha256(outside_file.read_bytes()).hexdigest(),
            'nested/nested.txt': hashlib.sha256(
                (outside_dir / 'nested.txt').read_bytes()).hexdigest(),
        }), encoding='utf-8')
        output = self.root / 'audit-links'
        result = self.run_audit(output)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        row = json.loads((output / 'results.json').read_text(encoding='utf-8'))[0]
        self.assertEqual(row['manifest_check']['status'], 'fail')
        self.assertEqual(row['manifest_check']['errors'], [
            'linked_path: alias.txt',
            'linked_path: nested/nested.txt',
        ])
        self.assertEqual(row['overall'], 'fail')


if __name__ == '__main__':
    unittest.main()
