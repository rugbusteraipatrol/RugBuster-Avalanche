"""Gunicorn settings for the public API, read from the working directory.

Kept here rather than on the command line: on 2026-09-11 the same flags were
added to the Procfile, the deploy served that commit, and gunicorn still
logged "Using worker: sync" -- the Railway service starts from its own
command. Gunicorn loads ./gunicorn.conf.py whichever command starts it.

Why these values: four /score requests sent together took 6, 11, 17 and 24 s
(one sync worker, each waiting for the one before), and the 30 s default
timeout killed slow scores and emptied the in-memory cache. A score is mostly
waiting on upstream reads, so threads carry the load; one worker keeps
SCAN_CACHE and the holder/creator caches shared.
"""

worker_class = "gthread"
workers = 1
threads = 8
timeout = 120
