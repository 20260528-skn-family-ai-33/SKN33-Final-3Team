"""Dataset namespaces shared by manual commands, scheduler jobs and ML readers."""
from contextlib import ExitStack
from functools import wraps
import inspect
from pathlib import Path

from .objects import ROOT, StorageError, enabled
from .workspace import _active, workspace

COLLECTORS = ROOT / 'backend/app/scheduler/collectors'
DATASETS = {
    'customs-monthly': ('raw/customs/monthly', ROOT / 'data/customs_load_outputs/monthly'),
    'customs-history': ('raw/customs/history', COLLECTORS / 'customs/customs_outputs'),
    'shortnews-daily': ('raw/shortnews/daily', ROOT / 'data/shortnews/daily'),
    'shortnews-history': ('raw/shortnews/history', COLLECTORS / 'shortnews/shortnews_outputs'),
    'news': ('raw/news', COLLECTORS / 'news/news_outputs'),
    'news-history': ('raw/news-history', ROOT / 'data/news'),
    'shortnews-processed': ('ml-inputs/shortnews-processed', ROOT / 'data/shortnews/pipeline_logs'),
    'external': ('ml-inputs/external', ROOT / 'data/external'),
    'hs-output': ('ml-inputs/hscode', COLLECTORS / 'hscode/hscode_outputs'),
    'hs-reference': ('ml-inputs/hs-reference', COLLECTORS / 'hscode/reference'),
    'keywords': ('ml-inputs/keywords', ROOT / 'data/news_keywords'),
    'un-runtime': ('raw/un-data/runtime', ROOT / 'data/un_data/runtime'),
    'un-inputs': ('raw/un-data/inputs', ROOT / 'runtime/s3-cache/un-inputs'),
    'models': ('models/monthly', ROOT / 'data/ml/monthly_models'),
    'model-candidates': ('models/candidates', ROOT / 'data/ml/candidates'),
    'model-versions': ('models/versions', ROOT / 'data/ml'),
}
TRAINING_INPUTS = ('customs-monthly', 'customs-history', 'shortnews-history',
                   'external', 'hs-output', 'hs-reference', 'keywords')


def training_inputs(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not enabled():
            return function(*args, **kwargs)
        with ExitStack() as stack:
            for name in TRAINING_INPUTS:
                prefix, root = DATASETS[name]
                stack.enter_context(workspace(root, prefix, write=False))
            return function(*args, **kwargs)
    return wrapped


def un_inputs(function):
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not enabled():
            return function(*args, **kwargs)
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        prefix, root = DATASETS['un-inputs']
        with workspace(root, prefix, write=False):
            for parameter, filename in (('scope_path', 'scope.json'), ('csv_path', 'un_data.csv')):
                if parameter in bound.arguments:
                    requested = Path(bound.arguments[parameter]).resolve()
                    if requested not in ((ROOT / 'data/un_data' / filename).resolve(), (root / filename).resolve()):
                        raise StorageError(f'S3 mode uses the migrated {filename}; an alternate local input is not allowed')
                    bound.arguments[parameter] = root / filename
            return function(*bound.args, **bound.kwargs)
    return wrapped


def collected(name, parameter):
    """Standalone collectors use the same namespace as scheduled parent pipelines."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            target = Path(bound.arguments[parameter]).resolve()
            for active in _active.get():
                if active.writer and target.is_relative_to(active.root):
                    result = function(*args, **kwargs)
                    active.publish()
                    return result
            prefix, root = DATASETS[name]
            if not target.is_relative_to(root.resolve()):
                raise StorageError(f'S3 collection output must be inside {root}')
            with workspace(root, prefix):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def managed_files(*parameters, defaults=None):
    """Resolve loader inputs from S3; an explicit path cannot silently bypass storage."""
    defaults = defaults or {}
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not enabled():
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            needed = set()
            for parameter in parameters:
                value = bound.arguments.get(parameter) or defaults.get(parameter)
                if value is None:
                    continue
                target = Path(value).resolve()
                if any(target.is_relative_to(ws.root) for ws in _active.get()):
                    continue
                matches = [(prefix, root) for prefix, root in DATASETS.values()
                           if target.is_relative_to(root.resolve()) and prefix.startswith(('raw/', 'ml-inputs/'))]
                if not matches:
                    raise StorageError(f'Input must belong to a migrated S3 dataset: {target}')
                needed.add(max(matches, key=lambda item: len(item[1].parts)))
            with ExitStack() as stack:
                for prefix, root in sorted(needed):
                    stack.enter_context(workspace(root, prefix, write=False))
                return function(*args, **kwargs)
        return wrapped
    return decorate
