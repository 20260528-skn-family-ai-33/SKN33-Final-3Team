import os
if os.environ.get('SHORTNEWS_OFFLINE_ROOT'):
    from offline_guard import install
    install()
