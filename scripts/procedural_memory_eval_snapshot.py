"""Byte-preserving, fixed-source snapshots for the isolated memory experiment.

No directory-wide source copy, environment inspection or provider access occurs.
A snapshot becomes valid only when its manifest is published last.  Consumers
must verify it before launching Python with ``-B`` and keep outputs outside it.
Hashes detect changes relative to the manifest; the caller should separately pin
the manifest hash if it needs to detect a coordinated replacement of both.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tempfile


SOURCE_FILES = (
    'scripts/eval_memory.py',
    'scripts/eval_procedural_memory.py',
    'scripts/procedural_memory_eval_domain.py',
    'scripts/eval_procedural_memory_induction.py',
    'scripts/procedural_memory_source_projection.py',
    'scripts/procedural_memory_eval_snapshot.py',
    'server/llm.py',
    'server/memory/presentation.py',
    'server/assistant/context.py',
    'server/assistant/notes.py',
    'server/assistant/note_proposals.py',
    'server/assistant/note_conversation.py',
    'eval/research/procedural_memory_eval_cases.json',
)
DIAGNOSTICS_SOURCE_FILES = SOURCE_FILES + (
    'scripts/eval_procedural_memory_diagnostics.py',
    'eval/research/procedural_memory_diagnostic_inputs_v1.json',
)
BOUNDARY_SOURCE_FILES = SOURCE_FILES + (
    'scripts/eval_procedural_memory_boundaries.py',
    'eval/research/procedural_memory_boundary_cases_v1.json',
)
MANIFEST_NAME = 'snapshot_manifest.json'


class SnapshotError(RuntimeError):
    """An unsafe path, unstable source or invalid snapshot was refused."""


def _profile_files(profile):
    if profile is None:
        return SOURCE_FILES
    if profile == 'diagnostics_v1':
        return DIAGNOSTICS_SOURCE_FILES
    if profile == 'boundaries_v1':
        return BOUNDARY_SOURCE_FILES
    raise SnapshotError('unknown_snapshot_profile')


def _linked(metadata):
    return (stat.S_ISLNK(metadata.st_mode)
            or bool(getattr(metadata, 'st_file_attributes', 0)
                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))


def _path(value):
    path = Path(value)
    if '..' in path.parts:
        raise SnapshotError('parent_traversal_refused')
    return path.absolute()


def _checked_existing(path, *, directory=False):
    # lstat every ancestor: resolve alone would quietly follow a junction.
    for member in (*reversed(path.parents), path):
        try:
            metadata = member.lstat()
        except OSError as error:
            raise SnapshotError('missing_or_unreadable_path') from error
        if _linked(metadata):
            raise SnapshotError('linked_path_refused')
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise SnapshotError('directory_required')
    return path.resolve(strict=True)


def _inside(path, root):
    return path == root or root in path.parents


def _validate_locations(source_root, destination):
    source = _checked_existing(_path(source_root), directory=True)
    output = _path(destination)
    parent = _checked_existing(output.parent, directory=True)
    output = parent / output.name
    if os.path.lexists(output):
        raise SnapshotError('destination_exists')
    if _inside(source, output):
        raise SnapshotError('source_destination_overlap')
    if _inside(output, source):
        relative = output.relative_to(source).parts
        # Explicit experiment-output exception; never write among source files.
        if (len(relative) < 4 or relative[:2] != ('eval', 'research')
                or relative[-1] != 'snapshot'):
            raise SnapshotError('unsafe_destination_within_source')
    return source, output


def _source_path(root, relative):
    path = root.joinpath(*relative.split('/'))
    try:
        resolved = _checked_existing(path)
    except SnapshotError as error:
        raise SnapshotError(f'source_file_unavailable:{relative}') from error
    if not _inside(resolved, root) or not stat.S_ISREG(path.lstat().st_mode):
        raise SnapshotError(f'unsafe_source_file:{relative}')
    return path


def _signature(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_ctime_ns)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _read_stable(root, relative):
    path = _source_path(root, relative)
    before = _signature(path.stat())
    first = path.read_bytes()
    middle = _signature(path.stat())
    second = path.read_bytes()
    after = _signature(path.stat())
    _source_path(root, relative)
    if before != middle or middle != after or _digest(first) != _digest(second):
        raise SnapshotError(f'source_changed:{relative}')
    return first, before


def _recheck_sources(source, captured):
    for relative, (expected_hash, expected_state) in captured.items():
        data, state = _read_stable(source, relative)
        if _digest(data) != expected_hash or state != expected_state:
            raise SnapshotError(f'source_changed:{relative}')


def _remove_owned_tree(path, identity):
    # Only remove the exact directory created by this call, never a replacement.
    if (os.path.lexists(path) and path.resolve() == path
            and not _linked(path.lstat())
            and _signature(path.stat())[:2] == identity):
        shutil.rmtree(path)


def create_snapshot(source_root, destination, *, profile=None):
    """Copy a fixed source profile and return the committed JSON manifest.

    The default preserves the original 13-file, schema-1 contract.  The explicit
    ``diagnostics_v1`` profile adds the diagnostic runner and fixed public inputs
    and ``boundaries_v1`` adds the boundary runner and its fixed specification;
    both use schema 2.  Consumers never derive their whitelist from the manifest.
    The destination must be absent and its parent must already exist.  Within
    the source root it must be ``eval/research/<run>/snapshot`` (or deeper under
    that run); an external destination is also accepted.  Source files are
    read twice and all sources are rechecked immediately before commitment.
    Failed copies remove only directories exclusively created by this call.
    """
    source_files = _profile_files(profile)
    source, output = _validate_locations(source_root, destination)
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}.staging-', dir=output.parent))
    stage_identity = _signature(stage.stat())[:2]
    output_identity = None
    completed = False
    try:
        files, captured = [], {}
        for relative in source_files:
            data, state = _read_stable(source, relative)
            checksum = _digest(data)
            captured[relative] = (checksum, state)
            copied = stage.joinpath(*relative.split('/'))
            copied.parent.mkdir(parents=True, exist_ok=True)
            with copied.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            files.append({'path': relative, 'sha256': checksum, 'size_bytes': len(data)})
        manifest = {
            'schema_version': 1 if profile is None else 2,
            'files': files,
            'python_environment': {
                'implementation': platform.python_implementation(),
                'version': platform.python_version(),
                'version_info': list(sys.version_info[:3]),
            },
        }
        if profile is not None:
            manifest['profile'] = profile
        with (stage / MANIFEST_NAME).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        _recheck_sources(source, captured)
        _checked_existing(output.parent, directory=True)
        # Exclusive reservation works on Windows and POSIX without overwriting
        # a destination created concurrently.  The atomic manifest is the commit
        # marker; an incomplete directory can never pass verify_snapshot.
        try:
            output.mkdir()
        except FileExistsError as error:
            raise SnapshotError('destination_exists') from error
        output_identity = _signature(output.stat())[:2]
        for child in stage.iterdir():
            if child.name != MANIFEST_NAME:
                os.replace(child, output / child.name)
        _recheck_sources(source, captured)
        os.replace(stage / MANIFEST_NAME, output / MANIFEST_NAME)
        verified = verify_snapshot(output)
        completed = True
        return verified
    finally:
        _remove_owned_tree(stage, stage_identity)
        if output_identity is not None and not completed:
            _remove_owned_tree(output, output_identity)


def verify_snapshot(root):
    """Return a validated manifest, rejecting hash mismatches and extra files."""
    directory = _checked_existing(_path(root), directory=True)
    manifest_path = directory / MANIFEST_NAME
    try:
        _checked_existing(manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'),
                              parse_constant=lambda value: (_ for _ in ()).throw(
                                  ValueError(f'nonstandard_number:{value}')))
        if (not isinstance(manifest, dict)
                or type(manifest.get('schema_version')) is not int):
            raise ValueError('invalid_schema')
        if manifest['schema_version'] == 1:
            if set(manifest) != {'schema_version', 'files', 'python_environment'}:
                raise ValueError('invalid_schema')
            source_files = SOURCE_FILES
        elif manifest['schema_version'] == 2:
            if (set(manifest) != {'schema_version', 'profile', 'files', 'python_environment'}
                    or not isinstance(manifest['profile'], str)):
                raise ValueError('invalid_schema')
            source_files = _profile_files(manifest['profile'])
        else:
            raise ValueError('invalid_schema')
        if (not isinstance(manifest['files'], list)
                or len(manifest['files']) != len(source_files)):
            raise ValueError('invalid_schema')
        environment = manifest['python_environment']
        if (not isinstance(environment, dict)
                or set(environment) != {'implementation', 'version', 'version_info'}
                or any(not isinstance(environment[key], str) for key in ('implementation', 'version'))
                or not isinstance(environment['version_info'], list)
                or len(environment['version_info']) != 3
                or any(type(value) is not int or value < 0 for value in environment['version_info'])):
            raise ValueError('invalid_python_environment')
        for relative, entry in zip(source_files, manifest['files']):
            if (not isinstance(entry, dict)
                    or set(entry) != {'path', 'sha256', 'size_bytes'}
                    or entry['path'] != relative
                    or not isinstance(entry['sha256'], str)
                    or len(entry['sha256']) != 64
                    or any(char not in '0123456789abcdef' for char in entry['sha256'])
                    or type(entry['size_bytes']) is not int or entry['size_bytes'] < 0):
                raise ValueError('invalid_file_entry')
    except (OSError, ValueError, TypeError, KeyError, SnapshotError) as error:
        raise SnapshotError('invalid_snapshot_manifest') from error
    allowed = set(source_files) | {MANIFEST_NAME}
    for base, directories, filenames in os.walk(directory, followlinks=False):
        for name in directories + filenames:
            member = Path(base) / name
            if _linked(member.lstat()):
                raise SnapshotError('linked_snapshot_member')
        for name in filenames:
            if (Path(base) / name).relative_to(directory).as_posix() not in allowed:
                raise SnapshotError('unexpected_snapshot_member')
    for entry in manifest['files']:
        try:
            data, _ = _read_stable(directory, entry['path'])
        except (OSError, SnapshotError) as error:
            raise SnapshotError(f'snapshot_file_unavailable:{entry["path"]}') from error
        if len(data) != entry['size_bytes'] or _digest(data) != entry['sha256']:
            raise SnapshotError(f'snapshot_hash_mismatch:{entry["path"]}')
    return manifest
