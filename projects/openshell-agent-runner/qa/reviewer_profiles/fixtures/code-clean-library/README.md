# Slug helper

This small internal library converts display names into URL-safe ASCII slugs.
Empty or punctuation-only names are rejected because callers use the result as a
database key. Python 3.12 is the only supported runtime.

Run the checks with `python3 -m unittest discover -s tests`.
