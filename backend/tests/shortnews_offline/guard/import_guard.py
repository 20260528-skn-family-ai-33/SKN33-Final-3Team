import sys,json
from pathlib import Path
def verify_modules(repo,out):
 rows={}
 for name,module in list(sys.modules.items()):
  p=getattr(module,'__file__',None)
  if not p:
   assert not (name.startswith('backend.') and not hasattr(module,'__path__')), ('project module without source',name)
   continue
  path=Path(p).resolve()
  if name=='backend' or name.startswith(('backend.','news.')):
   assert path.is_relative_to(repo), (name,str(path))
   rows[name]=str(path)
 out.write_text(json.dumps({'other_checkout_modules':0,'modules':rows},indent=2))
