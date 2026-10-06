"""Runtime migration never resumes jobs or carries shared purchase credentials."""
import fcntl
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('restore_runtime', Path(__file__).parents[1] / 'scripts/restore_local_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.fixture
def migration(tmp_path, monkeypatch):
    source, target = tmp_path / 'archive', tmp_path / 'new'
    target.mkdir()
    (source / 'infra/masumi').mkdir(parents=True)
    (source / 'infra/masumi/.env').write_text('ENCRYPTION_KEY=original-test-key\n')
    (source / '.env').write_text('CARDANO_CARD_TOKEN=local-test-token\nAGENTCARD_CLIENT_SECRET=must-not-copy\nCARDANO_CARD_MODE=preprod\nCARDANO_CARD_DB=/old/path.db\n')
    monkeypatch.setattr(runtime, 'ROOT', target)
    monkeypatch.setattr(runtime, 'require_ignored', lambda path: None)
    return source, target


def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (phase TEXT)')
        db.execute("INSERT INTO jobs VALUES ('reconciling')")
    Path(str(path) + '.lockfile').touch()


def test_restore_preserves_uncertain_job_and_excludes_agentcard(migration):
    source, target = migration
    make_db(source / 'data/agentcard-sandbox.db')
    report = runtime.restore(source)
    env = (target / '.env').read_text()
    assert 'must-not-copy' not in env
    assert 'CARDANO_CARD_MODE=local' in env
    assert 'CARDANO_CARD_DB=data/jobs.db' in env
    assert (target / 'infra/masumi/.env').read_bytes() == (source / 'infra/masumi/.env').read_bytes()
    with sqlite3.connect(target / 'data/agentcard-sandbox.db') as db:
        assert db.execute('SELECT phase FROM jobs').fetchone() == ('reconciling',)
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
    assert (target / '.env').stat().st_mode & 0o777 == 0o600
    assert (target / 'data/agentcard-sandbox.db').stat().st_mode & 0o777 == 0o600
    assert report['jobs_resumed'] is False
    assert report['agentcard_credentials_copied'] is False


def test_locked_source_is_skipped(migration):
    source, target = migration
    make_db(source / 'data/agentcard-sandbox.db')
    lock = Path(str(source / 'data/agentcard-sandbox.db') + '.lockfile')
    with lock.open('rb') as owner:
        fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = runtime.restore(source)
    assert report['active_databases_skipped'] == ['agentcard-sandbox.db']
    assert not (target / 'data/agentcard-sandbox.db').exists()


def test_existing_job_state_is_never_overwritten(migration):
    source, target = migration
    make_db(source / 'data/agentcard-sandbox.db')
    runtime.restore(source)
    with sqlite3.connect(target / 'data/agentcard-sandbox.db') as db:
        db.execute("UPDATE jobs SET phase='manual_review'")
    runtime.restore(source)
    with sqlite3.connect(target / 'data/agentcard-sandbox.db') as db:
        assert db.execute('SELECT phase FROM jobs').fetchone() == ('manual_review',)


def test_changed_node_credentials_do_not_replace_existing(migration):
    source, target = migration
    runtime.restore(source)
    (source / 'infra/masumi/.env').write_text('ENCRYPTION_KEY=changed-key\n')
    with pytest.raises(ValueError, match='refusing overwrite'):
        runtime.restore(source)
    assert 'original-test-key' in (target / 'infra/masumi/.env').read_text()
