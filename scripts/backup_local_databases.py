"""Online SQLite snapshots before an explicitly authorized local service restart."""
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
import json
root = Path(__file__).resolve().parents[1]
source = root / 'data'
destination = source / 'backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
destination.mkdir(parents=True, exist_ok=False)
results = []
for pattern in ('*.db', 'durable/*.sqlite'):
    for path in sorted(source.glob(pattern)):
        relative = str(path.relative_to(source)).replace('\\', '/')
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10) as original:
            with sqlite3.connect(target, timeout=10) as snapshot:
                original.backup(snapshot)
                result = snapshot.execute('PRAGMA integrity_check').fetchone()[0]
                if result != 'ok':
                    raise RuntimeError(f'{relative}: {result}')
        results.append({'database': relative, 'snapshot': str(target), 'integrity': result})
(destination / 'manifest.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
