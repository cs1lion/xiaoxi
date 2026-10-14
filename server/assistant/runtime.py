"""Durable agent checkpoints and audit events; no hidden reasoning is stored."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class RuntimeStore:
    def __init__(self, path: str | Path = ':memory:', clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY, status TEXT NOT NULL, checkpoint TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_confirmations (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, action_hash TEXT NOT NULL,
            status TEXT NOT NULL, expires_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS agent_input_waits (
            session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
            expires_at REAL NOT NULL);
        ''')

    def create(self, state: dict[str, Any]) -> str:
        run_id = uuid.uuid4().hex
        now = self.clock()
        with self.lock, self.db:
            self.db.execute('INSERT INTO agent_runs VALUES (?, ?, ?, ?, ?)',
                            (run_id, 'running', json.dumps(state, ensure_ascii=False), now, now))
        return run_id

    def save(self, run_id: str, status: str, state: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute('UPDATE agent_runs SET status=?, checkpoint=?, updated_at=? WHERE id=?',
                            (status, json.dumps(state, ensure_ascii=False), self.clock(), run_id))
            if status not in ('running', 'waiting_input'):
                self.db.execute('DELETE FROM agent_input_waits WHERE run_id=?', (run_id,))

    def input_for_session(self, session_id: str) -> dict[str, Any] | None:
        """Read a live input wait or its claimed run; never joins anonymous calls."""
        if not isinstance(session_id, str) or not session_id:
            return None
        with self.lock:
            row = self.db.execute('''SELECT r.id, r.status, r.checkpoint, w.expires_at
                FROM agent_input_waits w JOIN agent_runs r ON r.id=w.run_id
                WHERE w.session_id=? AND (r.status='running' OR
                    (r.status='waiting_input' AND w.expires_at>?))''',
                (session_id, self.clock())).fetchone()
        if row is None:
            return None
        state = json.loads(row['checkpoint'])
        return {'run_id': row['id'], 'status': row['status'],
                'text': state.get('last_clarification', ''), 'expires_at': row['expires_at']}

    def waiting_input(self, session_id: str) -> dict[str, Any] | None:
        """Side-effect-free routing snapshot; actual continuation uses a CAS claim."""
        item = self.input_for_session(session_id)
        return item if item and item['status'] == 'waiting_input' else None

    def wait_for_input(self, run_id: str, state: dict[str, Any], ttl: float = 900) -> float:
        """Publish the checkpoint and session binding in one write transaction."""
        now = self.clock()
        expires_at = now + ttl
        state['input_expires_at'] = expires_at
        session_id = state.get('context', {}).get('interaction_session_id', '')
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            expired = self.db.execute('''SELECT run_id FROM agent_input_waits
                WHERE expires_at<=? AND run_id IN
                    (SELECT id FROM agent_runs WHERE status='waiting_input')''', (now,)).fetchall()
            for row in expired:
                self.db.execute("UPDATE agent_runs SET status='input_expired', updated_at=? WHERE id=?",
                                (now, row['run_id']))
                self.db.execute('DELETE FROM agent_input_waits WHERE run_id=?', (row['run_id'],))
            if isinstance(session_id, str) and session_id:
                previous = self.db.execute('SELECT run_id FROM agent_input_waits WHERE session_id=?',
                                           (session_id,)).fetchone()
                if previous is not None and previous['run_id'] != run_id:
                    raise ValueError('session_already_waiting')
                self.db.execute('''INSERT INTO agent_input_waits VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET expires_at=excluded.expires_at''',
                    (session_id, run_id, expires_at))
            self.db.execute('UPDATE agent_runs SET status=?, checkpoint=?, updated_at=? WHERE id=?',
                            ('waiting_input', json.dumps(state, ensure_ascii=False), now, run_id))
        return expires_at

    def claim_input(self, run_id: str, session_id: str, text: str, *,
                    context: dict[str, Any] | None = None, cancel: bool = False,
                    max_rounds: int = 8, max_steps: int = 4) -> dict[str, Any]:
        """Atomically consume one input wait across connections, before any model call."""
        error = None
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT status,checkpoint FROM agent_runs WHERE id=?', (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            state = json.loads(row['checkpoint'])
            if row['status'] != 'waiting_input':
                raise ValueError('run_not_waiting_input')
            original_session = state.get('context', {}).get('interaction_session_id', '')
            if not session_id or session_id != original_session:
                raise ValueError('input_session_mismatch')
            now = self.clock()
            if state.get('input_expires_at', 0) <= now:
                status, error = 'input_expired', 'input_expired'
            elif state.get('input_rounds', 0) >= max_rounds and not cancel:
                status, error = 'limit_reached', 'input_limit_reached'
            else:
                state.setdefault('input_history', [{'role': 'user', 'content': state['user_text']}]).append(
                    {'role': 'user', 'content': text})
                state['latest_user_text'] = text
                state['input_rounds'] = state.get('input_rounds', 0) + 1
                state['step_limit'] = state['steps'] + max_steps
                if context is not None:
                    # The session binding cannot be changed by a continuation caller.
                    state['context'] = {**context, 'interaction_session_id': original_session}
                status = 'cancelled' if cancel else 'running'
                self.db.execute('INSERT INTO agent_events (run_id,kind,payload,created_at) VALUES (?,?,?,?)',
                                (run_id, 'input_cancelled' if cancel else 'input_received',
                                 json.dumps({'text': text}, ensure_ascii=False), now))
            cursor = self.db.execute('''UPDATE agent_runs SET status=?,checkpoint=?,updated_at=?
                WHERE id=? AND status='waiting_input' ''',
                (status, json.dumps(state, ensure_ascii=False), now, run_id))
            if cursor.rowcount != 1:
                raise ValueError('input_already_claimed')
            if status != 'running':
                self.db.execute('DELETE FROM agent_input_waits WHERE run_id=?', (run_id,))
        if error:
            raise ValueError(error)
        return state

    def get(self, run_id: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute('SELECT * FROM agent_runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return {'id': row['id'], 'status': row['status'], 'state': json.loads(row['checkpoint'])}

    def event(self, run_id: str, kind: str, payload: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute('INSERT INTO agent_events (run_id,kind,payload,created_at) VALUES (?,?,?,?)',
                            (run_id, kind, json.dumps(payload, ensure_ascii=False), self.clock()))

    def events(self, run_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute('SELECT * FROM agent_events WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
        return [{'kind': r['kind'], 'payload': json.loads(r['payload']), 'created_at': r['created_at']} for r in rows]

    def confirmation(self, run_id: str, action_hash: str, ttl: float = 600) -> dict[str, Any]:
        item = {'id': uuid.uuid4().hex, 'run_id': run_id, 'action_hash': action_hash,
                'status': 'pending', 'expires_at': self.clock() + ttl}
        with self.lock, self.db:
            self.db.execute('INSERT INTO agent_confirmations VALUES (:id,:run_id,:action_hash,:status,:expires_at)', item)
        return item

    def consume(self, confirmation_id: str, action_hash: str, approve: bool) -> str:
        """Single-use CAS; stale/changed actions fail closed."""
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM agent_confirmations WHERE id=?', (confirmation_id,)).fetchone()
            if row is None:
                raise KeyError(confirmation_id)
            if row['status'] != 'pending':
                raise ValueError('confirmation_already_used')
            if row['expires_at'] <= self.clock():
                raise ValueError('confirmation_expired')
            if row['action_hash'] != action_hash:
                raise ValueError('confirmation_action_changed')
            cursor = self.db.execute('UPDATE agent_confirmations SET status=? WHERE id=? AND status=?',
                                    ('approved' if approve else 'rejected', confirmation_id, 'pending'))
            if cursor.rowcount != 1:
                raise ValueError('confirmation_already_used')
            return row['run_id']

    def recover_interrupted(self) -> int:
        """Do not replay running mutations after a process crash."""
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE agent_runs SET status='interrupted', updated_at=? WHERE status='running'",
                                     (self.clock(),))
            self.db.execute('''DELETE FROM agent_input_waits WHERE run_id IN
                (SELECT id FROM agent_runs WHERE status='interrupted')''')
            return cursor.rowcount

    def close(self) -> None:
        self.db.close()
