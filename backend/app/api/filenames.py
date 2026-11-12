"""Keep each selected scope identifiable within the filesystem's filename limit."""
import re


def filename_part(value, byte_limit=60):
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', '_', str(value))
    text = re.sub(r'\s+', ' ', text).strip(' ._') or '전체'
    return text.encode('utf-8')[:byte_limit].decode('utf-8', errors='ignore').rstrip(' ._')


def selection_part(items, default=''):
    if not items:
        return default
    suffix = f' 외 {len(items) - 1}개' if len(items) > 1 else ''
    return filename_part(items[0], 60 - len(suffix.encode('utf-8'))) + suffix
