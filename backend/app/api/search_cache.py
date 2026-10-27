"""Bounded per-process TTL cache with same-key request coalescing."""
from collections import OrderedDict
from copy import deepcopy
from threading import RLock, Lock
from time import monotonic


class SearchCache:
    def __init__(self, maximum=256, ttl=300, clock=monotonic):
        self.maximum, self.ttl, self.clock=maximum,ttl,clock
        self.entries=OrderedDict()
        self.lock=RLock()
        self.stripes=[Lock() for _ in range(32)]

    def get_or_load(self,key,loader,*,cache_if=None):
        with self.stripes[hash(key)%len(self.stripes)]:
            with self.lock:
                found=self.entries.get(key)
                if found and found[0]>self.clock():
                    self.entries.move_to_end(key)
                    return deepcopy(found[1])
                self.entries.pop(key,None)
            value=loader()  # exceptions are not cached
            if cache_if is not None and not cache_if(value):
                return value
            with self.lock:
                self.entries[key]=(self.clock()+self.ttl,deepcopy(value))
                self.entries.move_to_end(key)
                while len(self.entries)>self.maximum: self.entries.popitem(last=False)
            return value
