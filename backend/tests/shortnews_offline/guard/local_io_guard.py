"""Standalone deny-only I/O guard; no project module substitution."""
import socket
def block_external_io(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('OFFLINE: network and unreviewed scheduler start forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(socket,'getaddrinfo',deny)
    from apscheduler.schedulers.base import BaseScheduler
    monkeypatch.setattr(BaseScheduler,'start',deny)
