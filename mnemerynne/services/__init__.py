"""Mnemerynne as one Python application per feature (docs/plans/services-rewrite.md).

The web app was one Python process, and Python runs one thread's Python code at a time: on the box everything
queued for one core. Each busy feature becomes a service of its own, a process (or several) on a port of its
own behind nginx, built from one table (registry.py) by one factory (factory.py). They share this package's
domain code and the database files; they don't call each other over HTTP.

`mnemerynne serve` with no service still runs everything in one process (the "all-in-one" web app,
web/app.py create_app): tests, development and small installs use it.
"""
