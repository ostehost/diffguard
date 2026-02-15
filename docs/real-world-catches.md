# Real-World Catches: DiffGuard vs. Historical OSS Bugs

> These are real commits in real repos where DiffGuard flags exactly what went wrong — before users found out the hard way.

---

## 1. 🏆 Flask: `redirect()` default changed from 302 → 303

**Repo:** [pallets/flask](https://github.com/pallets/flask)
**Commit:** `eca5fd1d` (merged via PR [#5898](https://github.com/pallets/flask/pull/5898))
**Issue:** [#5895](https://github.com/pallets/flask/issues/5895)
**Milestone:** Flask 3.2.0

### What changed

The `redirect()` function's `code` parameter default changed from `302` to `303`:

```python
# Before
def redirect(location: str, code: int = 302, ...) -> BaseResponse:

# After
def redirect(location: str, code: int = 303, ...) -> BaseResponse:
```

### Why it matters

HTTP 302 and 303 have subtly different semantics. 302 *sometimes* preserves the HTTP method (browser-dependent), while 303 *always* converts to GET. Any caller relying on 302's method-preservation behavior (e.g., API endpoints expecting POST→POST redirects) would silently break — no errors, just different behavior.

### DiffGuard's output

> Signature display simplified for readability — run the command yourself to see parameter type annotations.

```
⚠ DiffGuard: 2 changes need review

1. DEFAULT VALUE CHANGED: redirect(location, code=302, Response) → redirect(location, code=303, Response)
   File: src/flask/helpers.py:241
   Impact: 7 callers rely on the default:
     auth.py:25   `return redirect(url_for("auth.login"))`
     auth.py:77   `return redirect(url_for("auth.login"))`
     auth.py:105  `return redirect(url_for("index"))`
     blog.py:81   `return redirect(url_for("blog.index"))`
   Review: Verify callers expect the new default value

2. DEFAULT VALUE CHANGED: App.redirect(self, location, code=302) → App.redirect(self, location, code=303)
   File: src/flask/sansio/app.py:935
   Impact: 7 callers rely on the default
   Review: Verify callers expect the new default value
```

### Why this is a great story

- Flask is one of the most popular Python web frameworks (~70k GitHub stars)
- The change is intentional but **silently breaks callers** — no TypeError, no warning
- DiffGuard identifies the exact callers that rely on the default and need verification
- A human reviewer could easily miss the behavioral difference between 302 and 303

**Headline: "DiffGuard would have caught Flask PR #5898 before it shipped."**

---

## 2. httpx: `Request(method=)` narrows from `str | bytes` to `str`

**Repo:** [encode/httpx](https://github.com/encode/httpx)
**Commit:** `6622553` (PR [#3378](https://github.com/encode/httpx/pull/3378))

### What changed

The `Request.__init__()` `method` parameter type was narrowed from `str | bytes` to `str`:

```python
# Before
class Request:
    def __init__(self, method: str | bytes, url: URL | str, ...):

# After
class Request:
    def __init__(self, method: str, url: URL | str, ...):
```

### Why it matters

Any code passing `method=b"GET"` (bytes) would break with an `AttributeError` on `method.upper()` at runtime. The PR author acknowledged this was "nominally an API change" but believed it was a "bugfix in practice." Still — silent breakage for anyone using bytes.

### DiffGuard's output

```
⚠ DiffGuard: 1 change needs review

1. SIGNATURE CHANGED: __init__(self, method: str | bytes, ...) → __init__(self, method: str, ...)
   File: httpx/_models.py:311
   Impact: 63 callers rely on the default
   Review: Check all callers handle the new signature
```

### Why this is a great story

- httpx is the modern Python HTTP client (~13k stars), used by FastAPI's test client
- Type narrowing is exactly the kind of "looks harmless" change that breaks real code
- DiffGuard catches it instantly — no need to read the diff line-by-line

---

## 3. Pydantic: `@serializer` renamed to `@field_serializer` (symbol removed)

**Repo:** [pydantic/pydantic](https://github.com/pydantic/pydantic)
**Commit:** `11edcb2c` (PR [#5331](https://github.com/pydantic/pydantic/pull/5331))

### What changed

The `@serializer` decorator was renamed to `@field_serializer`, and a new `@model_serializer` was added alongside it. The old name was removed entirely.

### DiffGuard's output

```
⚠ DiffGuard: 5 changes need review

1. PARAMETER ADDED (BREAKING): make_generic_field_serializer(serializer, mode)
   → make_generic_field_serializer(serializer, mode, type)
   Impact: Breaking change — callers will break with missing required argument

2. SYMBOL REMOVED: serializer(__field, *fields, ...)
   File: pydantic/decorators.py:341
   Impact: 19 callers will break

3-4. (additional overloads of the removed symbol)

5. SYMBOL REMOVED: serializer(__field, *fields, mode='wrap', ...)
   Impact: 19 callers will break
```

### Why this is a great story

- Pydantic v2 was the biggest Python library migration in recent memory
- DiffGuard catches both the symbol removal AND identifies 19 internal callers that reference it
- This is the kind of rename that grep can find, but DiffGuard does it *automatically* as part of review

---

## Dogfooding Notes

While running DiffGuard on these repos, I noted:

1. **Missed catch — Django `UniqueConstraint(name=None)` → `UniqueConstraint(name)` (commit `b172cbdf33`):** A parameter changed from optional (`name=None`) to required (`name`). DiffGuard returned exit 0 with no findings. This is a false negative — removing a default value is a breaking change. **Filed as a potential improvement.**

2. **Output verbosity on httpx proxy commit:** 14 findings for the proxies→proxy migration. The output is very long. A summary mode or grouping related changes (e.g., "proxy parameter added to 9 HTTP method functions") would help for large refactors.

3. **Caller detection quality:** DiffGuard correctly identifies callers in both source and test files, which is excellent. The Flask example showing `auth.py` and `blog.py` callers makes the impact immediately tangible.

4. **Speed:** All reviews completed in under 5 seconds on these repos. Fast enough for CI integration.
