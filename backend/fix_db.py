import sqlite3, os, shutil, sys

data_dir = r'C:\Users\Administrator\AppData\Roaming\shipinhao-desktop\data'
db = os.path.join(data_dir, 'app.db')
backup = os.path.join(data_dir, 'app.db.backup_20260630_131843')

try:
    # Remove all db files
    for f in [db, db+'-wal', db+'-shm']:
        if os.path.exists(f):
            os.remove(f)
            print(f'Removed {f}')

    # Copy backup
    shutil.copy2(backup, db)
    print(f'Copied backup to {db}')

    # Open and verify
    conn = sqlite3.connect(db)
    print('Journal mode:', conn.execute('PRAGMA journal_mode').fetchone())
    conn.execute('PRAGMA journal_mode=DELETE')
    print('After DELETE:', conn.execute('PRAGMA journal_mode').fetchone())
    print('Integrity:', conn.execute('PRAGMA integrity_check').fetchone())
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    print('Tables:', [t[0] for t in tables])
    conn.close()
    print('Done - database restored successfully')
except Exception as e:
    print(f'Error: {e}', file=sys.stderr)
    sys.exit(1)
