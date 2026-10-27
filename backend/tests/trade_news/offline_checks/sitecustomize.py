import os
if os.environ.get('TRADE_NEWS_OFFLINE_ROOT'):
    from environment import install
    install()
