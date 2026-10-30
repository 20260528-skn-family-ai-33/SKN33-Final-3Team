"""File storage boundary. Future S3 implementation uses the same metadata contract."""
import hashlib
from pathlib import Path
from backend.app.api.common import require, now


class LocalReportStorage:
    def __init__(self, root): self.root=Path(root).resolve()

    def path(self, key):
        target=(self.root/key).resolve()
        require(target.is_relative_to(self.root),'파일 경로를 확인하세요.','NOT_FOUND',404)
        return target

    def write(self,key,content):
        target=self.path(key); target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_suffix(target.suffix+'.tmp')
        temporary.write_bytes(content); temporary.replace(target)
        return {'storage':'local','key':key,'sha256':hashlib.sha256(content).hexdigest(),
            'created_at':now().isoformat()+'Z'}

    def read(self,metadata):
        require(metadata.get('storage')=='local','파일 저장소 연결이 준비되지 않았습니다.','FEATURE_NOT_READY',503)
        target=self.path(metadata['key'])
        require(target.is_file(),'파일이 없습니다.','NOT_FOUND',404)
        content=target.read_bytes()
        require(hashlib.sha256(content).hexdigest()==metadata['sha256'],'파일 무결성을 확인하세요.','FILE_UNAVAILABLE',503)
        return content
