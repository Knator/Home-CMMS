"""Housekeeping for a self-hosted instance: backups, storage, integrity, database.

Kept out of the routes so each operation can be tested directly, and so the
destructive ones can be run in "scan" mode before anything is deleted.
"""
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime, timezone

from flask import current_app

from app.extensions import db
from app.models.attachment import Attachment, ENTITY_TYPES
from app.utils import thumbnail_dir, thumbnails_available

BACKUP_PREFIX = 'home-cmms-backup-'
BACKUP_SUFFIX = '.tar.gz'
# Written automatically just before a restore replaces everything.
SAFETY_PREFIX = 'pre-restore-'


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
        'backups_path': backup_dir(),
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
        if not is_backup_name(name):
            continue
        path = os.path.join(backup_dir(), name)
        stat = os.stat(path)
        out.append({
            'name': name,
            'size': stat.st_size,
            # A safety copy is taken automatically before a restore. It is an
            # ordinary backup and is listed as one, but labelled so the reason
            # it appeared is obvious.
            'automatic': name.startswith(SAFETY_PREFIX),
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
    if not name.endswith(BACKUP_SUFFIX):
        return False
    if not name.startswith((BACKUP_PREFIX, SAFETY_PREFIX)):
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
    # Safety copies are excluded: they exist to undo a restore, and pruning
    # them away is exactly the moment someone needs one.
    backups = [b for b in list_backups() if not b['automatic']]
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


# ---------------------------------------------------------------------------
# Restore
#
# The dangerous one: it replaces the database and every uploaded file. Three
# things make that survivable —
#
#   * the archive is validated before anything is touched, including every tar
#     member, because an archive is untrusted input and a crafted one can
#     otherwise write outside the directory it is extracted into;
#   * a safety copy of the current state is taken first, so a restore performed
#     by mistake is itself recoverable;
#   * migrations run afterwards, so a backup from an older version is brought
#     up to date rather than left unreadable.
# ---------------------------------------------------------------------------

DB_MEMBER = 'home_cmms.db'
UPLOAD_PREFIX = 'uploads/'


class RestoreError(Exception):
    """A backup that cannot safely be restored. The message is shown to the user."""


def _safe_members(archive):
    """Yield the archive's members, rejecting anything that could escape.

    Path traversal through tar entries is an old and very much live bug class:
    a member named ../../etc/cron.d/x, or a symlink pointing outside the tree,
    turns "extract a backup" into "write anywhere the process can".
    """
    for member in archive.getmembers():
        name = member.name
        if name.startswith('/') or os.path.isabs(name):
            raise RestoreError(f'Archive contains an absolute path: {name}')
        if '..' in name.replace('\\', '/').split('/'):
            raise RestoreError(f'Archive contains a parent-directory path: {name}')
        if member.issym() or member.islnk():
            raise RestoreError(f'Archive contains a link, which is not allowed: {name}')
        if not (member.isfile() or member.isdir()):
            raise RestoreError(f'Archive contains a special file: {name}')
        if name != DB_MEMBER and not name.startswith(UPLOAD_PREFIX) and name != 'uploads':
            raise RestoreError(f'Archive contains an unexpected entry: {name}')
        yield member


def inspect_backup(path):
    """Validate an archive and describe what restoring it would give you.

    Read-only: nothing is changed. Raises RestoreError with a readable reason.
    """
    if not tarfile.is_tarfile(path):
        raise RestoreError('That file is not a .tar.gz archive.')

    with tarfile.open(path, 'r:gz') as archive:
        members = list(_safe_members(archive))
        names = {m.name for m in members}
        if DB_MEMBER not in names:
            raise RestoreError(f'The archive has no {DB_MEMBER}, so it is not a '
                               'Home CMMS backup.')

        upload_members = [m for m in members if m.name.startswith(UPLOAD_PREFIX) and m.isfile()]
        with tempfile.TemporaryDirectory() as scratch:
            archive.extract(DB_MEMBER, scratch, filter='data')
            extracted = os.path.join(scratch, DB_MEMBER)
            summary = _describe_database(extracted)

        summary['attachment_files'] = len(upload_members)
        summary['upload_bytes'] = sum(m.size for m in upload_members)
        return summary


def _describe_database(path):
    """Read counts and the schema version out of a candidate database."""
    try:
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    except sqlite3.Error as error:
        raise RestoreError(f'The database in the archive could not be opened ({error}).')

    try:
        tables = {row[0] for row in connection.execute(
            "select name from sqlite_master where type='table'")}
        if 'alembic_version' not in tables:
            raise RestoreError('The database in the archive has no schema version, '
                               'so it is not a Home CMMS backup.')

        revision = connection.execute('select version_num from alembic_version').fetchone()
        counts = {}
        for table in ('users', 'assets', 'locations', 'work_orders', 'attachments'):
            if table in tables:
                counts[table] = connection.execute(
                    f'select count(*) from {table}').fetchone()[0]

        if 'users' not in tables:
            raise RestoreError('The database in the archive has no users table.')

        return {'revision': revision[0] if revision else None, 'counts': counts}
    except sqlite3.DatabaseError as error:
        raise RestoreError(f'The archive contains a corrupt database ({error}).')
    finally:
        connection.close()


def _safety_snapshot():
    """Archive the current state before replacing it, so a mistake is undoable."""
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    name = f'{SAFETY_PREFIX}{stamp}{BACKUP_SUFFIX}'
    target = os.path.join(backup_dir(), name)

    db_path = database_path()
    snapshot = os.path.join(backup_dir(), f'.safety-{stamp}.db')
    try:
        source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        try:
            source.execute('VACUUM INTO ?', (snapshot,))
        finally:
            source.close()

        uploads = current_app.config['UPLOAD_FOLDER']
        thumbs = os.path.basename(os.path.abspath(thumbnail_dir()))
        with tarfile.open(target, 'w:gz') as tar:
            tar.add(snapshot, arcname=DB_MEMBER)
            if os.path.isdir(uploads):
                for entry in sorted(os.listdir(uploads)):
                    if entry == thumbs:
                        continue
                    tar.add(os.path.join(uploads, entry), arcname=f'uploads/{entry}')
    finally:
        if os.path.exists(snapshot):
            os.remove(snapshot)
    return name


def restore_backup(path, take_safety_copy=True):
    """Replace the database and uploads with the contents of an archive.

    Returns a summary of what was restored. Raises RestoreError before touching
    anything if the archive is not usable.
    """
    summary = inspect_backup(path)          # validates; raises before any change

    db_path = database_path()
    if not db_path:
        raise RestoreError('Restore is only supported for SQLite databases.')

    uploads = current_app.config['UPLOAD_FOLDER']

    # A failed safety copy must not block the restore. The common reason it
    # fails is that the current database is corrupt or unreadable — which is
    # precisely when someone is restoring. Losing the undo is a worse outcome
    # than not restoring only if the current state was worth keeping, and a
    # database that cannot be read is not.
    safety, safety_error = None, None
    if take_safety_copy:
        try:
            safety = _safety_snapshot()
        except Exception as error:
            current_app.logger.warning(
                'Could not archive the current state before restoring: %s', error)
            safety_error = str(error)

    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(path, 'r:gz') as archive:
            archive.extractall(scratch, members=list(_safe_members(archive)),
                               filter='data')

        # Close pooled connections so the file underneath can be swapped, and
        # drop any session state pointing at the old database.
        db.session.remove()
        db.engine.dispose()

        shutil.copy2(os.path.join(scratch, DB_MEMBER), db_path)
        # Sidecars belong to the replaced database; leaving them behind would
        # graft the old write-ahead log onto the new file.
        for suffix in ('-wal', '-shm'):
            sidecar = f'{db_path}{suffix}'
            if os.path.exists(sidecar):
                os.remove(sidecar)

        restored_uploads = os.path.join(scratch, 'uploads')
        if os.path.isdir(restored_uploads):
            previous = f'{uploads}.replaced'
            shutil.rmtree(previous, ignore_errors=True)
            if os.path.isdir(uploads):
                os.rename(uploads, previous)
            shutil.copytree(restored_uploads, uploads)
            shutil.rmtree(previous, ignore_errors=True)

        db.engine.dispose()

    # A backup from an older version needs bringing forward; without this the
    # app would run against a schema it no longer understands.
    from flask_migrate import upgrade as alembic_upgrade
    alembic_upgrade()

    summary['safety_copy'] = safety
    summary['safety_error'] = safety_error
    return summary


def rotate_secret_key():
    """Invalidate every existing session after a restore.

    Sessions are signed cookies carrying a user id. The restored database can
    map that id to a different account, so a cookie issued before the restore
    must stop being accepted. Changing the signing key is what does that.

    Returns False when the key comes from the environment, which this cannot
    change — the caller should say so.
    """
    import secrets as secrets_module

    from config import SECRET_KEY_FILE

    if os.environ.get('SECRET_KEY', '').strip():
        return False

    new_key = secrets_module.token_hex(32)
    os.makedirs(os.path.dirname(SECRET_KEY_FILE), exist_ok=True)
    with open(SECRET_KEY_FILE, 'w', encoding='utf-8') as handle:
        handle.write(new_key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    current_app.config['SECRET_KEY'] = new_key
    current_app.secret_key = new_key
    return True
