"""Housekeeping for a self-hosted instance: backups, storage, integrity, database.

Kept out of the routes so each operation can be tested directly, and so the
destructive ones can be run in "scan" mode before anything is deleted.
"""
import os
import shutil
import sqlite3
import tarfile
import time
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.attachment import Attachment, ENTITY_TYPES
from app.utils import thumbnail_dir, thumbnails_available

BACKUP_PREFIX = 'home-cmms-backup-'
BACKUP_SUFFIX = '.tar.gz'


# ── paths ──────────────────────────────────────────────────────────────────

def database_path():
    """Filesystem path of the SQLite database, or None for other backends."""
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
    prefix = 'sqlite:///'
    if not uri.startswith(prefix):
        return None
    path = uri[len(prefix):]
    return path if path and path != ':memory:' else None


def backup_dir():
    path = os.path.join(os.path.dirname(database_path() or '.'), 'backups')
    os.makedirs(path, exist_ok=True)
    return path


def directory_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


# ── system status ──────────────────────────────────────────────────────────

def _package_version(name):
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return 'unknown'


def system_status():
    """Everything the status panel shows. Read-only."""
    import sys
    from importlib import metadata

    db_path = database_path()
    uploads = current_app.config['UPLOAD_FOLDER']
    thumbs = thumbnail_dir()

    db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
    # WAL content is part of the database but lives in a sibling file.
    wal = f'{db_path}-wal' if db_path else None
    wal_size = os.path.getsize(wal) if wal and os.path.exists(wal) else 0

    usage = shutil.disk_usage(os.path.dirname(db_path) if db_path else '.')

    return {
        'python_version': sys.version.split()[0],
        # flask.__version__ is deprecated; ask the package metadata instead.
        'flask_version': _package_version('flask'),
        'sqlalchemy_version': _package_version('sqlalchemy'),
        'database_path': db_path,
        'database_size': db_size,
        'wal_size': wal_size,
        'uploads_path': uploads,
        'uploads_size': directory_size(uploads) if os.path.isdir(uploads) else 0,
        'thumbnail_size': directory_size(thumbs) if os.path.isdir(thumbs) else 0,
        'thumbnails_available': thumbnails_available(),
        'disk_total': usage.total,
        'disk_free': usage.free,
        'disk_used_pct': round((usage.total - usage.free) * 100 / usage.total, 1) if usage.total else 0,
        'counts': record_counts(),
    }


def record_counts():
    from app.models.asset import Asset
    from app.models.job_plan import JobPlan
    from app.models.location import Location
    from app.models.pm import PM
    from app.models.user import User
    from app.models.work_order import WorkOrder

    return {
        'Locations': Location.query.count(),
        'Assets': Asset.query.count(),
        'Work Orders': WorkOrder.query.count(),
        'Job Plans': JobPlan.query.count(),
        'PM Schedules': PM.query.count(),
        'Attachments': Attachment.query.count(),
        'Users': User.query.count(),
    }


def scheduler_status():
    """What the PM scheduler is doing — the thing that is otherwise invisible."""
    scheduler = current_app.extensions.get('pm_scheduler')
    if scheduler is None:
        return {'running': False, 'next_run': None,
                'reason': 'Not started in this process (SCHEDULER_ENABLED=0, or a CLI run).'}
    job = scheduler.get_job('pm_check')
    return {
        'running': scheduler.running,
        'next_run': getattr(job, 'next_run_time', None),
        'reason': None,
    }


# ── backups ────────────────────────────────────────────────────────────────

def list_backups():
    out = []
    for name in sorted(os.listdir(backup_dir()), reverse=True):
        if not (name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX)):
            continue
        path = os.path.join(backup_dir(), name)
        stat = os.stat(path)
        out.append({
            'name': name,
            'size': stat.st_size,
            # Normalised to naive UTC so it goes through the same display
            # conversion as every other timestamp.
            'created': datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                                .replace(tzinfo=None),
        })
    return out


def create_backup():
    """One archive holding the database and the uploaded files.

    The database is snapshotted with VACUUM INTO rather than copied: in WAL mode
    a plain file copy misses everything still in the -wal sidecar and silently
    produces a stale backup. Thumbnails are excluded because they regenerate.
    """
    db_path = database_path()
    if not db_path:
        raise RuntimeError('Backups are only supported for SQLite databases.')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    name = f'{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}'
    target = os.path.join(backup_dir(), name)
    snapshot = os.path.join(backup_dir(), f'.snapshot-{stamp}.db')

    started = time.time()
    try:
        source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        try:
            source.execute('VACUUM INTO ?', (snapshot,))
        finally:
            source.close()

        uploads = current_app.config['UPLOAD_FOLDER']
        thumbs = os.path.abspath(thumbnail_dir())

        def skip_thumbnails(info):
            return None if os.path.abspath(os.path.join(uploads, info.name)).startswith(thumbs) else info

        with tarfile.open(target, 'w:gz') as tar:
            tar.add(snapshot, arcname='home_cmms.db')
            if os.path.isdir(uploads):
                for entry in sorted(os.listdir(uploads)):
                    if entry == os.path.basename(thumbs):
                        continue
                    tar.add(os.path.join(uploads, entry), arcname=f'uploads/{entry}')
    finally:
        if os.path.exists(snapshot):
            os.remove(snapshot)

    return {'name': name, 'size': os.path.getsize(target),
            'seconds': round(time.time() - started, 2)}


def is_backup_name(name):
    """Only names this module generates, and nothing that escapes the directory."""
    if not (name.startswith(BACKUP_PREFIX) and name.endswith(BACKUP_SUFFIX)):
        return False
    if os.sep in name or (os.altsep and os.altsep in name) or '..' in name:
        return False
    return True


def delete_backup(name):
    """Remove one backup. The name is validated, never joined blindly."""
    if not is_backup_name(name):
        return False
    path = os.path.join(backup_dir(), name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    return True


def prune_backups(keep):
    """Delete all but the newest `keep` backups. Returns how many went."""
    backups = list_backups()
    removed = 0
    for entry in backups[keep:]:
        if delete_backup(entry['name']):
            removed += 1
    return removed


# ── storage integrity ──────────────────────────────────────────────────────
#
# Attachments are polymorphic (entity_type + entity_id, no foreign key), so the
# database cannot enforce that a row has a file or that a file has a row. Three
# things can drift apart, and all three are found by scanning rather than
# guessing:
#   * rows whose file has gone missing
#   * files on disk that no row points at
#   * cached thumbnails for attachments that no longer exist
# The scan is read-only; cleanup is a separate, explicit step.

def _entity_exists(entity_type, entity_id):
    from app.models.asset import Asset
    from app.models.job_plan import JobPlan
    from app.models.location import Location
    from app.models.pm import PM
    from app.models.work_order import WorkOrder

    model = {'location': Location, 'asset': Asset, 'work_order': WorkOrder,
             'job_plan': JobPlan, 'pm': PM}.get(entity_type)
    if model is None:
        return False
    return db.session.get(model, entity_id) is not None


def scan_storage():
    """Report drift between the attachment table and the filesystem."""
    uploads = current_app.config['UPLOAD_FOLDER']
    thumbs = thumbnail_dir()

    rows = Attachment.query.all()
    expected = {}
    missing_files = []
    orphaned_rows = []

    for att in rows:
        path = os.path.join(uploads, att.entity_type, str(att.entity_id), att.stored_filename)
        expected[os.path.abspath(path)] = att
        if not os.path.exists(path):
            missing_files.append(att)
        if not _entity_exists(att.entity_type, att.entity_id):
            orphaned_rows.append(att)

    stray_files = []
    stray_bytes = 0
    for entity_type in ENTITY_TYPES:
        base = os.path.join(uploads, entity_type)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for name in files:
                path = os.path.abspath(os.path.join(root, name))
                if path not in expected:
                    stray_files.append(os.path.relpath(path, uploads))
                    try:
                        stray_bytes += os.path.getsize(path)
                    except OSError:
                        pass

    known_ids = {att.id for att in rows}
    stray_thumbs = []
    stray_thumb_bytes = 0
    if os.path.isdir(thumbs):
        for name in os.listdir(thumbs):
            stem, _, ext = name.rpartition('.')
            if ext != 'jpg' or not stem.isdigit() or int(stem) not in known_ids:
                stray_thumbs.append(name)
                try:
                    stray_thumb_bytes += os.path.getsize(os.path.join(thumbs, name))
                except OSError:
                    pass

    return {
        'missing_files': missing_files,
        'orphaned_rows': orphaned_rows,
        'stray_files': stray_files,
        'stray_bytes': stray_bytes,
        'stray_thumbnails': stray_thumbs,
        'stray_thumbnail_bytes': stray_thumb_bytes,
        'clean': not (missing_files or orphaned_rows or stray_files or stray_thumbs),
    }


def clean_storage():
    """Act on what the scan found. Returns a summary of what was removed."""
    uploads = current_app.config['UPLOAD_FOLDER']
    thumbs = thumbnail_dir()
    report = scan_storage()
    removed = {'rows': 0, 'files': 0, 'thumbnails': 0, 'bytes': 0}

    # Rows with no file, and rows whose owning record has gone, are both dead.
    for att in {a.id: a for a in report['missing_files'] + report['orphaned_rows']}.values():
        path = os.path.join(uploads, att.entity_type, str(att.entity_id), att.stored_filename)
        if os.path.exists(path):
            removed['bytes'] += os.path.getsize(path)
            os.remove(path)
        thumb = os.path.join(thumbs, f'{att.id}.jpg')
        if os.path.exists(thumb):
            os.remove(thumb)
        db.session.delete(att)
        removed['rows'] += 1

    for relative in report['stray_files']:
        path = os.path.join(uploads, relative)
        if os.path.exists(path):
            removed['bytes'] += os.path.getsize(path)
            os.remove(path)
            removed['files'] += 1

    for name in report['stray_thumbnails']:
        path = os.path.join(thumbs, name)
        if os.path.exists(path):
            removed['bytes'] += os.path.getsize(path)
            os.remove(path)
            removed['thumbnails'] += 1

    db.session.commit()
    return removed


def clear_thumbnail_cache():
    """Drop every cached thumbnail; they rebuild on next view."""
    thumbs = thumbnail_dir()
    removed = freed = 0
    if os.path.isdir(thumbs):
        for name in os.listdir(thumbs):
            path = os.path.join(thumbs, name)
            if os.path.isfile(path):
                freed += os.path.getsize(path)
                os.remove(path)
                removed += 1
    return {'removed': removed, 'bytes': freed}


# ── database maintenance ───────────────────────────────────────────────────

def check_database():
    """SQLite's own consistency check, plus foreign key verification."""
    result = db.session.execute(db.text('PRAGMA integrity_check')).fetchall()
    integrity = [row[0] for row in result]
    fk_rows = db.session.execute(db.text('PRAGMA foreign_key_check')).fetchall()
    return {
        'ok': integrity == ['ok'] and not fk_rows,
        'integrity': integrity,
        'foreign_key_violations': len(fk_rows),
    }


def vacuum_database():
    """Rebuild the file to reclaim space freed by deletions.

    VACUUM cannot run inside a transaction, so the session is committed and the
    statement issued on a fresh autocommit connection.
    """
    path = database_path()
    if not path:
        raise RuntimeError('VACUUM is only supported for SQLite databases.')

    before = os.path.getsize(path)
    db.session.commit()
    engine = db.session.get_bind()
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.execute(db.text('VACUUM'))
    after = os.path.getsize(path)
    return {'before': before, 'after': after, 'reclaimed': max(before - after, 0)}


def checkpoint_wal():
    """Fold the write-ahead log back into the database file."""
    engine = db.session.get_bind()
    with engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        row = conn.execute(db.text('PRAGMA wal_checkpoint(TRUNCATE)')).fetchone()
    return {'busy': row[0] if row else None,
            'log_pages': row[1] if row else None,
            'checkpointed': row[2] if row else None}
