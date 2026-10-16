"""Offline snapshot boundaries; no credentials, production stores or providers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import procedural_memory_eval_snapshot as target


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / 'source'
        self.source.mkdir()
        for index, relative in enumerate(target.SOURCE_FILES):
            member = self.source / relative
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(f'# source {index}\r\n# 完整字节\r\n'.encode('utf-8'))
        self.output = self.base / 'snapshot'

    def add_diagnostic_sources(self):
        for index, relative in enumerate(target.DIAGNOSTICS_SOURCE_FILES[len(target.SOURCE_FILES):]):
            member = self.source / relative
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(f'# diagnostic {index}\r\n'.encode('utf-8'))

    def test_only_fixed_files_are_copied_byte_for_byte_without_secrets(self):
        self.add_diagnostic_sources()
        (self.source / '.env').write_text('API_KEY=SECRET_SENTINEL', encoding='utf-8')
        (self.source / 'data').mkdir()
        (self.source / 'data/user.db').write_bytes(b'SECRET_SENTINEL')
        oracle = 'eval/research/procedural_memory_eval_oracles.json'
        (self.source / oracle).write_bytes(b'HOST_ONLY_ORACLE_SENTINEL')
        with patch.dict(os.environ, {'UNRELATED_API_KEY': 'ENV_SECRET_SENTINEL'}):
            manifest = target.create_snapshot(self.source, self.output)
        self.assertEqual(manifest, target.verify_snapshot(self.output))
        self.assertEqual(manifest['schema_version'], 1)
        self.assertNotIn('profile', manifest)
        self.assertEqual([entry['path'] for entry in manifest['files']], list(target.SOURCE_FILES))
        actual = {member.relative_to(self.output).as_posix()
                  for member in self.output.rglob('*') if member.is_file()}
        self.assertEqual(actual, set(target.SOURCE_FILES) | {target.MANIFEST_NAME})
        self.assertEqual(len(target.SOURCE_FILES), 13)
        self.assertNotIn(oracle, target.SOURCE_FILES)
        self.assertFalse((self.output / oracle).exists())
        self.assertFalse((self.output / '.env').exists())
        for entry in manifest['files']:
            original = (self.source / entry['path']).read_bytes()
            self.assertEqual((self.output / entry['path']).read_bytes(), original)
            self.assertEqual(entry['size_bytes'], len(original))
            self.assertEqual(entry['sha256'], hashlib.sha256(original).hexdigest())
        self.assertNotIn('SECRET_SENTINEL', (self.output / target.MANIFEST_NAME).read_text())

    def test_diagnostic_profile_copies_only_its_fixed_files_and_verifies(self):
        self.add_diagnostic_sources()
        (self.source / '.env').write_text('API_KEY=SECRET_SENTINEL', encoding='utf-8')
        manifest = target.create_snapshot(self.source, self.output, profile='diagnostics_v1')
        self.assertEqual(manifest['schema_version'], 2)
        self.assertEqual(manifest['profile'], 'diagnostics_v1')
        self.assertEqual(len(manifest['files']), 15)
        self.assertEqual([entry['path'] for entry in manifest['files']],
                         list(target.DIAGNOSTICS_SOURCE_FILES))
        self.assertEqual(manifest, target.verify_snapshot(self.output))
        actual = {member.relative_to(self.output).as_posix()
                  for member in self.output.rglob('*') if member.is_file()}
        self.assertEqual(actual, set(target.DIAGNOSTICS_SOURCE_FILES) | {target.MANIFEST_NAME})
        for entry in manifest['files']:
            original = (self.source / entry['path']).read_bytes()
            self.assertEqual((self.output / entry['path']).read_bytes(), original)
            self.assertEqual(entry['sha256'], hashlib.sha256(original).hexdigest())

    def test_unknown_create_profile_is_refused_without_creating_output(self):
        with self.assertRaisesRegex(target.SnapshotError, 'unknown_snapshot_profile'):
            target.create_snapshot(self.source, self.output, profile='unregistered')
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('.snapshot.staging-*')))

    def test_diagnostic_profile_requires_both_added_source_files(self):
        self.add_diagnostic_sources()
        for relative in target.DIAGNOSTICS_SOURCE_FILES[len(target.SOURCE_FILES):]:
            with self.subTest(relative=relative):
                member = self.source / relative
                original = member.read_bytes()
                member.unlink()
                with self.assertRaisesRegex(target.SnapshotError, 'source_file_unavailable'):
                    target.create_snapshot(self.source, self.output, profile='diagnostics_v1')
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.base.glob('.snapshot.staging-*')))
                member.write_bytes(original)

    def test_diagnostic_manifest_requires_known_profile_and_exact_schema(self):
        self.add_diagnostic_sources()
        original = target.create_snapshot(self.source, self.output, profile='diagnostics_v1')
        manifest_path = self.output / target.MANIFEST_NAME
        invalid_variants = [
            {**original, 'profile': 'unregistered'},
            {**original, 'profile': None},
            {**original, 'profile': 1},
            {key: value for key, value in original.items() if key != 'profile'},
            {**original, 'schema_version': 1},
            {**original, 'schema_version': 3},
            {**original, 'extra': 'metadata cannot select source files'},
        ]
        for index, manifest in enumerate(invalid_variants):
            with self.subTest(variant=index):
                manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
                with self.assertRaisesRegex(target.SnapshotError, 'invalid_snapshot_manifest'):
                    target.verify_snapshot(self.output)
        manifest_path.write_text(json.dumps(original), encoding='utf-8')
        self.assertEqual(target.verify_snapshot(self.output), original)

    def test_diagnostic_manifest_cannot_add_remove_or_replace_fixed_entries(self):
        self.add_diagnostic_sources()
        original = target.create_snapshot(self.source, self.output, profile='diagnostics_v1')
        manifest_path = self.output / target.MANIFEST_NAME
        replacement = dict(original['files'][-1], path='eval/research/unregistered.json')
        invalid_entries = [
            original['files'][:-1],
            original['files'] + [replacement],
            original['files'][:-1] + [replacement],
        ]
        for index, entries in enumerate(invalid_entries):
            with self.subTest(variant=index):
                manifest_path.write_text(json.dumps({**original, 'files': entries}), encoding='utf-8')
                with self.assertRaisesRegex(target.SnapshotError, 'invalid_snapshot_manifest'):
                    target.verify_snapshot(self.output)
        manifest_path.write_text(json.dumps(original), encoding='utf-8')
        target.verify_snapshot(self.output)

    def test_diagnostic_snapshot_missing_and_extra_members_are_refused(self):
        self.add_diagnostic_sources()
        target.create_snapshot(self.source, self.output, profile='diagnostics_v1')
        for relative in target.DIAGNOSTICS_SOURCE_FILES[len(target.SOURCE_FILES):]:
            with self.subTest(relative=relative):
                member = self.output / relative
                original = member.read_bytes()
                member.unlink()
                with self.assertRaisesRegex(target.SnapshotError, 'snapshot_file_unavailable'):
                    target.verify_snapshot(self.output)
                member.write_bytes(original)
        (self.output / 'eval/research/unregistered.json').write_bytes(b'{}')
        with self.assertRaisesRegex(target.SnapshotError, 'unexpected_snapshot_member'):
            target.verify_snapshot(self.output)

    def test_source_changes_after_commit_do_not_change_snapshot(self):
        manifest = target.create_snapshot(self.source, self.output)
        first = target.SOURCE_FILES[0]
        frozen = (self.output / first).read_bytes()
        (self.source / first).write_bytes(b'changed source')
        self.assertEqual((self.output / first).read_bytes(), frozen)
        self.assertEqual(target.verify_snapshot(self.output), manifest)

    def test_copied_entrypoint_resolves_root_to_snapshot_even_from_source_cwd(self):
        entrypoint = 'scripts/eval_procedural_memory_induction.py'
        (self.source / entrypoint).write_text(
            'from pathlib import Path\nROOT = Path(__file__).resolve().parents[1]\nprint(ROOT)\n',
            encoding='utf-8')
        target.create_snapshot(self.source, self.output)
        result = subprocess.run([sys.executable, '-B', str(self.output / entrypoint)],
                                cwd=self.source, capture_output=True, text=True, check=True)
        self.assertEqual(Path(result.stdout.strip()), self.output)
        target.verify_snapshot(self.output)

    def test_existing_destination_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / 'existing.txt'
        sentinel.write_text('prior artifact', encoding='utf-8')
        with self.assertRaisesRegex(target.SnapshotError, 'destination_exists'):
            target.create_snapshot(self.source, self.output)
        self.assertEqual(sentinel.read_text(), 'prior artifact')

    def test_missing_source_refuses_before_publishing(self):
        (self.source / target.SOURCE_FILES[-1]).unlink()
        with self.assertRaisesRegex(target.SnapshotError, 'source_file_unavailable'):
            target.create_snapshot(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.base.glob('.snapshot.staging-*')))

    def test_source_ancestor_and_unapproved_source_descendant_are_refused(self):
        with self.assertRaisesRegex(target.SnapshotError, 'destination_exists'):
            target.create_snapshot(self.source, self.source)
        for destination in (self.source / 'unapproved', self.source / 'scripts/snapshot'):
            with self.assertRaisesRegex(target.SnapshotError, 'unsafe_destination_within_source'):
                target.create_snapshot(self.source, destination)
        with self.assertRaisesRegex(target.SnapshotError, 'destination_exists'):
            target.create_snapshot(self.source, self.base)

    def test_experiment_snapshot_destination_is_allowed_under_source(self):
        destination = self.source / 'eval/research/run/snapshot'
        destination.parent.mkdir()
        target.create_snapshot(self.source, destination)
        target.verify_snapshot(destination)

    def test_parent_traversal_and_missing_destination_parent_are_refused(self):
        with self.assertRaisesRegex(target.SnapshotError, 'parent_traversal_refused'):
            target.create_snapshot(self.source, self.base / 'unrelated/../snapshot')
        with self.assertRaisesRegex(target.SnapshotError, 'missing_or_unreadable_path'):
            target.create_snapshot(self.source, self.base / 'missing/snapshot')

    def test_change_during_source_read_is_detected(self):
        original_read = Path.read_bytes
        victim = self.source / target.SOURCE_FILES[0]
        changed = False

        def mutating_read(path):
            nonlocal changed
            data = original_read(path)
            if path == victim and not changed:
                changed = True
                path.write_bytes(b'changed while reading')
            return data

        with patch.object(Path, 'read_bytes', mutating_read):
            with self.assertRaisesRegex(target.SnapshotError, 'source_changed'):
                target.create_snapshot(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_source_change_after_its_copy_is_detected_by_final_recheck(self):
        original_read = Path.read_bytes
        victim = self.source / target.SOURCE_FILES[0]
        trigger = self.source / target.SOURCE_FILES[-1]
        changed = False

        def mutating_read(path):
            nonlocal changed
            data = original_read(path)
            if path == trigger and not changed:
                changed = True
                victim.write_bytes(b'changed after its initial copy')
            return data

        with patch.object(Path, 'read_bytes', mutating_read):
            with self.assertRaisesRegex(target.SnapshotError, 'source_changed'):
                target.create_snapshot(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_concurrent_destination_creation_is_not_overwritten(self):
        original_mkdir = Path.mkdir
        sentinel = self.output / 'concurrent.txt'

        def racing_mkdir(path, *args, **kwargs):
            if path == self.output:
                original_mkdir(path)
                sentinel.write_text('concurrent owner', encoding='utf-8')
            return original_mkdir(path, *args, **kwargs)

        with patch.object(Path, 'mkdir', racing_mkdir):
            with self.assertRaisesRegex(target.SnapshotError, 'destination_exists'):
                target.create_snapshot(self.source, self.output)
        self.assertEqual(sentinel.read_text(), 'concurrent owner')

    def test_source_change_during_publication_removes_only_own_incomplete_output(self):
        original_mkdir = Path.mkdir
        victim = self.source / target.SOURCE_FILES[0]

        def mutating_mkdir(path, *args, **kwargs):
            result = original_mkdir(path, *args, **kwargs)
            if path == self.output:
                victim.write_bytes(b'changed before manifest commitment')
            return result

        with patch.object(Path, 'mkdir', mutating_mkdir):
            with self.assertRaisesRegex(target.SnapshotError, 'source_changed'):
                target.create_snapshot(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(victim.is_file())

    def test_snapshot_tampering_missing_files_and_extra_files_are_detected(self):
        target.create_snapshot(self.source, self.output)
        member = self.output / target.SOURCE_FILES[0]
        original = member.read_bytes()
        member.write_bytes(b'tampered')
        with self.assertRaisesRegex(target.SnapshotError, 'snapshot_hash_mismatch'):
            target.verify_snapshot(self.output)
        member.unlink()
        with self.assertRaisesRegex(target.SnapshotError, 'snapshot_file_unavailable'):
            target.verify_snapshot(self.output)
        member.write_bytes(original)
        (self.output / '.env').write_text('not allowed', encoding='utf-8')
        with self.assertRaisesRegex(target.SnapshotError, 'unexpected_snapshot_member'):
            target.verify_snapshot(self.output)

    def test_manifest_cannot_expand_whitelist_or_use_nonstandard_numbers(self):
        target.create_snapshot(self.source, self.output)
        manifest_path = self.output / target.MANIFEST_NAME
        original = manifest_path.read_text(encoding='utf-8')
        manifest = json.loads(original)
        manifest['files'][0]['path'] = '../.env'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(target.SnapshotError, 'invalid_snapshot_manifest'):
            target.verify_snapshot(self.output)
        manifest_path.write_text(original.replace('"schema_version": 1', '"schema_version": NaN'),
                                 encoding='utf-8')
        with self.assertRaisesRegex(target.SnapshotError, 'invalid_snapshot_manifest'):
            target.verify_snapshot(self.output)

    def test_source_and_destination_parent_links_are_refused(self):
        external = self.base / 'external.py'
        external.write_bytes(b'outside source')
        member = self.source / target.SOURCE_FILES[0]
        member.unlink()
        try:
            member.symlink_to(external)
        except OSError as error:
            self.skipTest(f'symlink creation unavailable: {error.__class__.__name__}')
        # 无符号链接权限的 Windows 上 symlink_to 可能不报错、也不建链接，
        # 只落一个普通空文件——场景没建成，跳过而不是误判产品故障。
        if not member.is_symlink():
            self.skipTest('symlink creation degraded to a regular file; escape scenario not built')
        with self.assertRaisesRegex(target.SnapshotError, 'source_file_unavailable'):
            target.create_snapshot(self.source, self.output)
        member.unlink()
        member.write_bytes(b'restored')
        link = self.base / 'linked-parent'
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(target.SnapshotError, 'linked_path_refused'):
            target.create_snapshot(self.source, link / 'snapshot')

    def test_windows_reparse_attributes_are_refused_without_link_privileges(self):
        original_lstat = Path.lstat
        victim = self.source / target.SOURCE_FILES[0]

        def reparse_lstat(path):
            metadata = original_lstat(path)
            if path == victim:
                return SimpleNamespace(st_mode=metadata.st_mode, st_file_attributes=0x400)
            return metadata

        with patch.object(target.stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400, create=True):
            with patch.object(Path, 'lstat', reparse_lstat):
                with self.assertRaisesRegex(target.SnapshotError, 'source_file_unavailable'):
                    target.create_snapshot(self.source, self.output)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
