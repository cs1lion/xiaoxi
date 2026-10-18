"""Coverage map: which source modules have a dedicated test script?

Not a gate — a survey. A module listed as "no dedicated test" may still be
covered indirectly (through app.py or a service-level test), so the output
names only the modules with *no* obvious test entry point, for triage.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories whose contents are vendored or generated: no test expected.
SKIP_DIRS = ('__pycache__', 'vendor', 'node_modules', 'design-preview', 'shots')
SKIP_FILES = {'__init__.py', 'config.py'}


def module_stems() -> list[str]:
    stems = []
    for path in sorted((ROOT / 'server').rglob('*.py')):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        stems.append(path.stem)
    return stems


def test_bodies() -> dict[str, str]:
    bodies = {}
    for path in sorted((ROOT / 'scripts').glob('test_*.py')):
        try:
            bodies[path.name] = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
    return bodies


def main() -> int:
    stems = module_stems()
    bodies = test_bodies()
    all_tests = '\n'.join(bodies.values())
    uncovered = []
    indirect = []
    for stem in stems:
        # A test file whose name mentions the module, or any test that imports it.
        named = [name for name in bodies if stem in name.replace('test_', '')]
        imported = bool(re.search(rf'\b(?:from|import)\s+[\w.]*\b{stem}\b', all_tests))
        if named or imported:
            if not named:
                indirect.append(stem)
            continue
        uncovered.append(stem)
    print(f'server modules scanned: {len(stems)}')
    print(f'with a dedicated test file: {len(stems) - len(uncovered) - len(indirect)}')
    print(f'covered only indirectly: {len(indirect)}')
    print(f'no dedicated test: {len(uncovered)}')
    print('\n[no dedicated test]')
    for stem in uncovered:
        print(f'  {stem}')
    print('\n[covered only indirectly]')
    for stem in indirect:
        print(f'  {stem}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
