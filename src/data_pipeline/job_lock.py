"""Small cross-platform collector lock, independent of legacy provider imports."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def exclusive_job(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            acquire=lambda:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            release=lambda:msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            acquire=lambda:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            release=lambda:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        try:acquire()
        except OSError:raise RuntimeError('ANOTHER_COLLECTION_JOB_IS_RUNNING') from None
        try:yield
        finally:stream.seek(0);release()
