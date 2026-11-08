# Cedar Lexer

`openshell-research-cedar-lexer` is a [Pygments](https://pygments.org/) plugin
that highlights [Cedar](https://www.cedarpolicy.com/) policies, the alternate
policy language for OpenShell sandboxes. It also covers the temporal extension
from [Dogwood](https://github.com/dogwood-policy/dogwood), which builds on Cedar.
Pygments has no built-in Cedar lexer, so without this plugin Cedar code renders
as plain text.

The OpenShell Research documentation site installs it so Dev Notes can
highlight Cedar code blocks. It is internal to this repository and is not
published to PyPI. Other projects here can install it from its local path; it
works anywhere Pygments is used, including Python-Markdown, Zensical, and
`pygmentize`.

## What it highlights

- Cedar effects and conditions: `permit`, `forbid`, `when`, `unless`, and
  `if`/`then`/`else`.
- The operators `in`, `has`, `like`, and `is`, and the scope variables
  `principal`, `action`, `resource`, and `context`.
- `@annotations`, entity namespaces such as `Sandbox::Action::"HttpRequest"`,
  attribute access, method and extension-function calls, strings with escapes,
  and `?slot` placeholders.
- Dogwood's `when temporal` and `unless temporal` clauses; the keywords
  `guardrails`, `formerly`, `previous`, `since`, `within`, `exists`, `count`,
  `sum`, `for`, `where`, and `tp`; `def cedar` and `def temporal` macros;
  `$binder` references; and durations such as `24h`.

The plugin registers the `cedar` and `dogwood` aliases and the `*.cedar` and
`*.dw` filename patterns. Both aliases use the same lexer.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/) for development

## Install

The repository's `requirements-docs.txt` installs it for the documentation
build. To install it into another environment that runs Pygments, run this
from the repository root:

```sh
uv pip install ./projects/tools/cedar-lexer
```

A uv project in this repository can depend on it as a path source instead:

```sh
uv add --editable ../../tools/cedar-lexer
```

## Use

Pygments discovers the lexer through its plugin entry point, so no
configuration is needed. In Markdown, tag a fenced code block with `cedar`:

````markdown
```cedar
permit (
    principal is Sandbox::Process,
    action == Sandbox::Action::"NetworkConnect",
    resource == Sandbox::NetworkEndpoint::"api.github.com:443"
)
when { context.binary_path == "/usr/bin/gh" };
```
````

To highlight a file from the command line:

```sh
pygmentize -l cedar policy.cedar
```

To use the lexer directly from Python:

```python
from pygments import highlight
from pygments.formatters import HtmlFormatter

from openshell_research_cedar_lexer import CedarLexer

html = highlight("permit (principal, action, resource);", CedarLexer(), HtmlFormatter())
```

## Limitations

- The lexer highlights tokens; it does not parse or validate policies. Use the
  Cedar or Dogwood tooling to check that a policy is well formed.
- Characters that are not valid Cedar, such as `#`, produce Pygments `Error`
  tokens. Most HTML themes render those with a visible error style.
- Dogwood keywords are highlighted anywhere they appear, so a record key named
  `count` or `where` is shown as a keyword.
- An entity type without a namespace, such as `User` in `principal is User`, is
  shown as a plain name.
- Dogwood coverage follows its grammar as of November 2026. New Dogwood syntax
  needs a lexer update.

## Development

Run the checks from this directory:

```sh
make check
```

`make check` syncs the locked environment and runs Ruff formatting and lint
checks, `ty`, and pytest. Run `make help` for the individual targets.
