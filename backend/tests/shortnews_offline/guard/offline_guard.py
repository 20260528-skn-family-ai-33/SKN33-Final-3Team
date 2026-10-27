"""No network, real env/auth files, remote DB, or non-temporary Chroma in local tests."""
import builtins, io, os, socket, tempfile, sys
from pathlib import Path

_installed = False

def install():
    global _installed
    if _installed: return
    _installed = True
    root=Path(os.environ['SHORTNEWS_OFFLINE_ROOT']).resolve()
    tempfile.tempdir=str(root)
    repositories=[Path(os.environ['SCHEDULER_TEST_REPO']).resolve()]
    def audit(event,args):
        if event in ('socket.connect','socket.getaddrinfo'): raise AssertionError('OFFLINE: network denied')
        if event in ('open','os.mkdir','os.remove','os.rmdir','os.rename'):
            for arg in args[:2] if event=='os.rename' else args[:1]:
                if not isinstance(arg,(str,bytes,os.PathLike)): continue
                path=Path(os.fsdecode(arg)).resolve()
                if path.suffix.lower()=='.sql' and any(word in path.name.lower() for word in ('rds','접속','연결','인증','비밀','credential','secret','session')):
                    raise AssertionError('OFFLINE: connection SQL denied')
                if any(path.is_relative_to(repo/folder) for repo in repositories for folder in ('data','chroma_data','backend/app/scheduler/collectors/trade_news/data')):
                    raise AssertionError('OFFLINE: operating path denied')
                if not path.is_relative_to(root) and (path.name.startswith('.env') or path.name=='env' or path.name.endswith(('.pem','.key','.session.sql')) or '.aws' in path.parts):
                    raise AssertionError('OFFLINE: secret/session denied')
    sys.addaudithook(audit)

    def inside(path):
        try: return Path(path).resolve().is_relative_to(root)
        except (TypeError,ValueError): return False
    def deny(*args,**kwargs): raise AssertionError('OFFLINE: network access prohibited')
    socket.socket.connect=deny
    socket.socket.connect_ex=deny
    socket.create_connection=deny
    socket.getaddrinfo=deny
    def guard_open(original):
        def guarded(file,*args,**kwargs):
            if isinstance(file,(str,bytes,os.PathLike)):
                path=Path(os.fsdecode(file));name=path.name.lower()
                protected=name.startswith('.env') or name=='env' or path.suffix in {'.pem','.key'} or name in {'credentials','config.json'} and '.aws' in path.parts
                if protected and not inside(path): raise AssertionError('OFFLINE: configuration/credential read prohibited')
            return original(file,*args,**kwargs)
        return guarded
    builtins.open=guard_open(builtins.open);io.open=guard_open(io.open)
    import dotenv
    original_dotenv=dotenv.dotenv_values
    def values(path=None,*args,**kwargs):
        return original_dotenv(path,*args,**kwargs) if path and inside(path) else {}
    dotenv.dotenv_values=values
    import sqlalchemy
    create=sqlalchemy.create_engine
    def local_engine(url,*args,**kwargs):
        u=sqlalchemy.engine.make_url(url)
        if u.get_backend_name()!='sqlite' or u.database not in (None,'',':memory:') and not inside(u.database):
            raise AssertionError('OFFLINE: only isolated SQLite databases allowed')
        return create(url,*args,**kwargs)
    sqlalchemy.create_engine=local_engine
    import chromadb
    persistent=chromadb.PersistentClient
    def local_chroma(path='./chroma',*args,**kwargs):
        if not inside(path): raise AssertionError('OFFLINE: only temporary Chroma paths allowed')
        return persistent(path,*args,**kwargs)
    chromadb.PersistentClient=local_chroma
