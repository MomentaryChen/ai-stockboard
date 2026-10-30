"""Daily batch behind the 類股進場 page.

Kept out of `server/` on purpose. It needs pandas and several hundred megabytes
for a few minutes, while the API is a single process under a 1 GB ceiling that
also serves live traffic. The two sides share one JSON file and nothing else.
"""

#: Part of the stored key, so changing the method never rewrites old rows: a
#: later accuracy check must be able to tell which method made each call.
ENGINE_VERSION = "1.0.0"

#: Bumped when the JSON shape changes. The importer refuses a version it does
#: not know rather than storing fields under the wrong meaning.
SCHEMA_VERSION = 1
