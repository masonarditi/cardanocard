"""Copy an archived local runtime without credentials that can rotate AgentCard tokens.

Explicit invocation only. Existing destination files are never overwritten. SQLite
snapshots require the source's application lock to be free; no job is resumed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV_KEYS = {
    'CARDANO_CARD_TOKEN', 'CARDANO_CARD_POLL_SECONDS', 'FAKE_SCENARIO',
}
EVIDENCE_FILES = (
    'sandbox-acceptance-input.json', 'sandbox-acceptance-id.txt',
    'sandbox-request.json', 'sandbox-conversation.json', 'agentcard-merchants.json',
    'preprod-wallets.json', 'masumi-openapi.json', 'masumi-openapi-source.json',
)


def private_write(path: Path, content: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Refusing symlink destination')
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f'Existing file differs; refusing overwrite: {path.relative_to(ROOT)}')
        path.chmod(0o600)
        return False
    fd, temporary = tempfile.mkstemp(prefix='.runtime-copy-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.link(temporary, path)  # Atomic and refuses a raced destination.
    finally:
        os.unlink(temporary)
    return True


def require_ignored(path: Path) -> None:
    result = subprocess.run(['git', 'check-ignore', '--quiet', str(path)], cwd=ROOT)
    if result.returncode:
        raise ValueError(f'Destination must be ignored: {path.relative_to(ROOT)}')


def snapshot(source: Path, target: Path) -> bool:
    # Never replace a destination database: it may have advanced after migration.
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = Path(str(source) + '.lockfile')
    with ExitStack() as stack:
        if lock_path.exists():
            lock = stack.enter_context(lock_path.open('rb'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source_db = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
        stack.callback(source_db.close)
        fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=target.parent)
        os.close(fd)
        try:
            dest_db = sqlite3.connect(temporary)
            try:
                source_db.backup(dest_db)
                if dest_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('SQLite snapshot integrity failed')
            finally:
                dest_db.close()
            return private_write(target, Path(temporary).read_bytes())
        finally:
            os.unlink(temporary)


def restore(source: Path) -> dict:
    source = source.resolve()
    if source == ROOT or not (source / 'infra/masumi/.env').is_file():
        raise ValueError('Expected a separate archived integration with node configuration')
    for path in (ROOT / '.env', ROOT / 'infra/masumi/.env', ROOT / 'data/.probe', ROOT / 'work/.probe'):
        require_ignored(path)
    # Preserve only the demo's local authentication/settings. AgentCard credentials
    # are deliberately excluded, as are old path and real-mode overrides.
    local_lines = [line for line in (source / '.env').read_text().splitlines()
                   if line.partition('=')[0] in LOCAL_ENV_KEYS]
    local_lines += ['CARDANO_CARD_MODE=local', 'CARDANO_CARD_DB=data/jobs.db',
                    'CARDANO_CARD_PORT=8080', 'CARDANO_CARD_FRONTEND=false', 'PURCHASE_BACKEND=fake']
    private_write(ROOT / '.env', ('\n'.join(local_lines) + '\n').encode())
    node_data = (source / 'infra/masumi/.env').read_bytes()
    private_write(ROOT / 'infra/masumi/.env', node_data)
    if (ROOT / 'infra/masumi/.env').read_bytes() != node_data:
        raise ValueError('Node credentials do not match')
    restored = []
    active = []
    for name in ('agentcard-sandbox.db', 'replay-demo.db'):
        old = source / 'data' / name
        if old.is_file():
            try:
                if snapshot(old, ROOT / 'data' / name):
                    restored.append(name)
            except BlockingIOError:
                active.append(name)
    evidence = list(EVIDENCE_FILES)
    evidence += [str(p.relative_to(source / 'work')) for p in (source / 'work/sandbox-evidence').glob('*.json')]
    copied = 0
    for name in evidence:
        old = source / 'work' / name
        if old.is_file():
            copied += private_write(ROOT / 'work' / name, old.read_bytes())
    report = {'node_configuration_matches': True, 'databases_snapshotted': restored,
              'private_evidence_files_copied': copied, 'active_databases_skipped': active,
              'agentcard_credentials_copied': False, 'jobs_resumed': False,
              'containers_changed': False}
    report_path = ROOT / 'work/runtime-restoration.json'
    if not report_path.exists():
        private_write(report_path, json.dumps(report, indent=2).encode())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = restore(args.source)
    except BlockingIOError:
        raise SystemExit('Source database is active. Stop its owner before migration; no jobs were resumed.') from None
    except Exception:
        raise SystemExit('Runtime restoration stopped safely. Check ignored destinations and existing files; no secrets printed.') from None
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
