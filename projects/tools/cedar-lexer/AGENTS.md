# Agent instructions

Read `README.md` and `pyproject.toml` before changing this project.

## Compatibility

- The documentation build installs this project with pip from
  `requirements-docs.txt`. Keep it installable on Python 3.10 and keep Pygments
  as its only runtime dependency.
- Derive Cedar and Dogwood syntax from their published grammars, not from
  examples alone. Dogwood's grammars are in `dogwood-syntax/src/grammar.pest`
  and `dogwood-language/src/extension/temporal/grammar.pest` in
  [dogwood-policy/dogwood](https://github.com/dogwood-policy/dogwood).
- When adding syntax, add a test that checks its token types. Valid policies
  must never produce `Error` tokens; the repository's
  `tests/test_cedar_code_blocks.py` enforces this for every published `cedar`
  or `dogwood` code block.

## Dependencies

- Use `uv`. `pyproject.toml` and `uv.lock` define the dependencies.
- Use `uv add` or `uv remove` to change dependencies. Do not edit `uv.lock` by
  hand.

## Checks

Run these commands from this directory:

```sh
make check
```

Report any command that could not run and why.
