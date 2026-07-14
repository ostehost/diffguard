"""Tests for the end-to-end pipeline."""

from __future__ import annotations

import pytest

from diffguard.engine._types import MatchedSymbol, Symbol, compute_body_hash
from diffguard.engine.pipeline import _apply_moves, run_pipeline
from diffguard.schema import FileChange, DiffGuardOutput, SymbolChange


SIMPLE_DIFF = """\
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,5 +1,8 @@
 def greet(name: str) -> str:
     return f"Hello {name}"
+
+def farewell(name: str) -> str:
+    return f"Goodbye {name}"
"""

OLD_UTILS = """\
def greet(name: str) -> str:
    return f"Hello {name}"
"""

NEW_UTILS = """\
def greet(name: str) -> str:
    return f"Hello {name}"

def farewell(name: str) -> str:
    return f"Goodbye {name}"
"""


def _content_provider(
    old_files: dict[str, str],
    new_files: dict[str, str],
    old_ref: str = "abc",
    new_ref: str = "def",
) -> object:
    def _get(ref: str, path: str) -> str | None:
        if ref == old_ref:
            return old_files.get(path)
        if ref == new_ref:
            return new_files.get(path)
        return None

    return _get


def test_simple_function_add() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert isinstance(result, DiffGuardOutput)
    assert result.meta.ref_range == "abc..def"
    assert result.meta.stats.files == 1
    assert len(result.files) == 1
    fc = result.files[0]
    assert fc.path == "utils.py"
    assert fc.language == "python"
    added = [c for c in fc.changes if c.kind == "function_added"]
    assert len(added) == 1
    assert added[0].name == "farewell"


def test_generated_file_skipped() -> None:
    diff = """\
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
-  "version": "1.0.0"
+  "version": "1.1.0"
"""
    result = run_pipeline(diff, "a..b")
    assert result.files[0].generated is True
    assert result.files[0].changes == []


def test_unsupported_language() -> None:
    diff = """\
diff --git a/readme.md b/readme.md
--- a/readme.md
+++ b/readme.md
@@ -1,2 +1,3 @@
 # Title
+Some text
"""
    result = run_pipeline(diff, "a..b")
    assert result.files[0].unsupported_language is True


def test_no_content_provider() -> None:
    result = run_pipeline(SIMPLE_DIFF, "a..b", get_content=None)
    assert len(result.files) == 1
    assert result.files[0].language == "python"
    assert result.files[0].changes == []


def test_healthy_diff_has_no_warnings() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert result.meta.warnings == []


def test_mixed_valid_and_malformed_records_warn_analysis_is_incomplete() -> None:
    malformed_record = """\
diff --git a/broken.py
--- a/broken.py
+++ b/broken.py
@@ -1 +1 @@
-old
+new
"""

    result = run_pipeline(SIMPLE_DIFF + malformed_record, "abc..def")

    assert [file.path for file in result.files] == ["utils.py"]
    assert result.meta.stats.files == 1
    assert result.meta.warnings == [
        "diff contains file headers that could not be parsed — analysis incomplete"
    ]


def test_missing_new_content_warns_and_skips_analysis() -> None:
    # The diff says utils.py was modified, but the new-side content can't be
    # fetched (e.g. git show failed). The pipeline must surface a warning rather
    # than fabricate "everything removed" findings from an empty baseline.
    get = _content_provider({"utils.py": OLD_UTILS}, {})  # new side unavailable
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert result.files[0].changes == []
    assert any("utils.py" in w and "content unavailable" in w for w in result.meta.warnings)


def test_missing_old_content_warns_and_skips_analysis() -> None:
    # The other disjunct of the guard: the modified file's *pre-image* blob is
    # unfetchable (shallow clone / gc'd commit) while the new side is present.
    # Without the guard this would fabricate "everything added" findings.
    get = _content_provider({}, {"utils.py": NEW_UTILS})  # old side unavailable
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert result.files[0].changes == []
    assert any("utils.py" in w and "content unavailable" in w for w in result.meta.warnings)


def test_invalid_utf8_snapshot_content_warns_and_skips_analysis(tmp_path) -> None:
    import subprocess

    from diffguard.git import get_file_at_ref

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    path = tmp_path / "contract.py"
    path.write_text("def contract(value: int) -> int:\n    return value\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@t.com",
            "-c",
            "core.hooksPath=",
            "commit",
            "-m",
            "valid",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    path.write_bytes(b"def contract(value):\n    return value\n\xff\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@t.com",
            "-c",
            "core.hooksPath=",
            "commit",
            "-m",
            "invalid",
        ],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1 +1 @@
-old
+new
"""

    result = run_pipeline(
        diff,
        "HEAD~1..HEAD",
        lambda ref, file_path: get_file_at_ref(ref, file_path, repo_path=tmp_path),
    )

    assert result.files[0].changes == []
    assert result.meta.warnings == [
        "contract.py: content unavailable at ref — symbol analysis skipped"
    ]


def test_summary_has_focus() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.summary.focus) >= 1
    assert "farewell" in result.summary.focus[0]


def test_tiered_oneliner_not_empty() -> None:
    get = _content_provider({"utils.py": OLD_UTILS}, {"utils.py": NEW_UTILS})
    result = run_pipeline(SIMPLE_DIFF, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.tiered.oneliner) > 0


def test_breaking_change_in_summary() -> None:
    old_src = "def process(x: int) -> str:\n    return str(x)\n"
    new_src = "def process(x: int, y: int) -> str:\n    return str(x + y)\n"
    diff = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1,2 +1,2 @@
-def process(x: int) -> str:
-    return str(x)
+def process(x: int, y: int) -> str:
+    return str(x + y)
"""
    get = _content_provider({"lib.py": old_src}, {"lib.py": new_src})
    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.summary.breaking_changes) == 1
    assert result.summary.breaking_changes[0].name == "process"
    assert "BREAKING" in result.tiered.oneliner


@pytest.mark.parametrize(
    ("old_signature", "new_signature", "category_id", "breaking"),
    [
        ("def contract(a=1)", "def contract(a=2)", "default_changed", False),
        ("def contract(a=1)", "def contract(a)", "default_removed", True),
        (
            "def contract(a=1)",
            "def contract(a, b=2)",
            "default_removed",
            True,
        ),
        ("def contract(a=1, /)", "def contract(a)", "default_removed", True),
        ("def contract(a)", "def contract(a, b)", "required_parameter_added", True),
        ("def contract(a, b)", "def contract(a)", "parameter_removed", True),
        ("def contract(a, b)", "def contract(b, a)", "parameter_reordered", True),
        (
            "def contract(a: int)",
            "def contract(a: str)",
            "parameter_annotation_changed",
            None,
        ),
        (
            "def contract(*args, value)",
            "def contract(value, *args)",
            "parameter_kind_changed",
            True,
        ),
        (
            "def contract(value)",
            "def contract(*args, value)",
            "parameter_kind_changed",
            True,
        ),
        (
            "def contract(a) -> int",
            "def contract(a) -> str",
            "return_annotation_changed",
            None,
        ),
    ],
)
def test_pure_signature_edits_are_classified_independently_of_body(
    old_signature: str,
    new_signature: str,
    category_id: str,
    breaking: bool | None,
) -> None:
    old_src = f"{old_signature}:\n    return 1\n"
    new_src = f"{new_signature}:\n    return 1\n"
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1,2 +1,2 @@
-old
+new
"""
    get = _content_provider({"contract.py": old_src}, {"contract.py": new_src})
    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]
    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.category_id == category_id
    assert change.breaking is breaking


def test_truly_unchanged_symbol_is_excluded() -> None:
    source = "def contract(a=1) -> int:\n    return 1\n"
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1 +1 @@
-same
+same
"""
    get = _content_provider({"contract.py": source}, {"contract.py": source})
    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]
    assert result.files[0].changes == []


def test_python_signature_formatting_only_edit_is_excluded() -> None:
    old_src = "def contract(value:int=1)->int:\n    return value\n"
    new_src = "def contract( value: int = 1 ) -> int:\n    return value\n"
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1,2 +1,2 @@
-def contract(value:int=1)->int:
+def contract( value: int = 1 ) -> int:
     return value
"""
    get = _content_provider({"contract.py": old_src}, {"contract.py": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


def test_typescript_signature_formatting_only_edit_is_excluded() -> None:
    old_src = "function contract<T extends Base>(value:T): number { return 1; }\n"
    new_src = "function contract< T extends Base >( value : T ):number { return 1; }\n"
    diff = """\
diff --git a/contract.ts b/contract.ts
--- a/contract.ts
+++ b/contract.ts
@@ -1 +1 @@
-function contract<T extends Base>(value:T): number { return 1; }
+function contract< T extends Base >( value : T ):number { return 1; }
"""
    get = _content_provider({"contract.ts": old_src}, {"contract.ts": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


def test_tsx_signature_change_and_removal_are_analyzed_with_jsx() -> None:
    old_src = """\
export function Widget(label: string) {
    return <button>{label}</button>;
}
export function obsolete() {
    return null;
}
"""
    new_src = """\
export function Widget(label: string, count: number) {
    return <button>{label}: {count}</button>;
}
"""
    diff = """\
diff --git a/component.tsx b/component.tsx
--- a/component.tsx
+++ b/component.tsx
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({"component.tsx": old_src}, {"component.tsx": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    file_change = result.files[0]
    assert file_change.language == "typescript"
    assert file_change.parse_error is False
    assert result.meta.warnings == []
    assert [(change.name, change.kind) for change in file_change.changes] == [
        ("Widget", "signature_changed"),
        ("obsolete", "function_removed"),
    ]
    assert file_change.changes[0].category_id == "parameter_added"


@pytest.mark.parametrize(
    ("path", "old_src", "new_src", "symbol", "before_signature", "after_signature"),
    [
        (
            "contract.ts",
            "export function contract(value) { return value; }\n",
            "function contract(value) { return value; }\n",
            "contract",
            "export function contract(value)",
            "function contract(value)",
        ),
        (
            "contract.ts",
            "export default function contract(value) { return value; }\n",
            "function contract(value) { return value; }\n",
            "contract",
            "export default function contract(value)",
            "function contract(value)",
        ),
        (
            "contract.js",
            "export default function contract(value) { return value; }\n",
            "export function contract(value) { return value; }\n",
            "contract",
            "export default function contract(value)",
            "export function contract(value)",
        ),
        (
            "contract.js",
            "export class Contract {}\n",
            "class Contract {}\n",
            "Contract",
            "export class Contract",
            "class Contract",
        ),
        (
            "contract.ts",
            "export default class Contract {}\n",
            "class Contract {}\n",
            "Contract",
            "export default class Contract",
            "class Contract",
        ),
        (
            "contract.js",
            "export default class Contract {}\n",
            "export class Contract {}\n",
            "Contract",
            "export default class Contract",
            "export class Contract",
        ),
        (
            "contract.js",
            "export const contract = (value) => value;\n",
            "const contract = (value) => value;\n",
            "contract",
            "export const contract = (value) =>",
            "const contract = (value) =>",
        ),
    ],
)
def test_removing_export_state_surfaces_as_signature_change(
    path: str,
    old_src: str,
    new_src: str,
    symbol: str,
    before_signature: str,
    after_signature: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    changes = [change for change in result.files[0].changes if change.name == symbol]
    assert len(changes) == 1
    assert changes[0].kind == "signature_changed"
    assert changes[0].before_signature == before_signature
    assert changes[0].after_signature == after_signature
    assert changes[0].breaking is None


@pytest.mark.parametrize(
    ("path", "old_src", "new_src"),
    [
        (
            "contract.js",
            "export default function contract(value) { return value; }\n",
            "export /* retained */ default function contract( value ) { return value; }\n",
        ),
        (
            "contract.ts",
            "export class Contract extends Base {}\n",
            "export class Contract  extends  Base {}\n",
        ),
        (
            "contract.js",
            "export const contract = (value) => value;\n",
            "export const contract = ( value ) => value;\n",
        ),
    ],
)
def test_export_preserving_formatting_only_edit_is_excluded(
    path: str,
    old_src: str,
    new_src: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


@pytest.mark.parametrize(
    ("path", "old_src", "new_src"),
    [
        (
            "contract.ts",
            "function contract(value:string/* old ), /[)]/, `x` */,next=1):number "
            "{ return value.length + next; }\n",
            "function contract(value:string/* new ], /[},]/, `y` */,next=1):number "
            "{ return value.length + next; }\n",
        ),
        (
            "contract.js",
            "function contract(\n  value=1, // old ), /[)]/, `x`\n  next=2\n) "
            "{ return value + next; }\n",
            "function contract(\n  value=1, // new ], /[},]/, `y`\n  next=2\n) "
            "{ return value + next; }\n",
        ),
        (
            "contract.go",
            "package contract\nfunc Contract(value int /* old ), /[)]/, `x` */, next int) int "
            "{ return value + next }\n",
            "package contract\nfunc Contract(value int /* new ], /[},]/, `y` */, next int) int "
            "{ return value + next }\n",
        ),
    ],
)
def test_non_python_signature_comment_only_edit_is_excluded(
    path: str,
    old_src: str,
    new_src: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


def test_typescript_template_default_formatting_only_edit_is_excluded() -> None:
    old_src = "function contract(value=`${a / b}`,other:string):number { return 1; }\n"
    new_src = "function contract( value = `${a / b}` , other : string ) : number { return 1; }\n"
    diff = """\
diff --git a/contract.ts b/contract.ts
--- a/contract.ts
+++ b/contract.ts
@@ -1 +1 @@
-function contract(value=`${a / b}`,other:string):number { return 1; }
+function contract( value = `${a / b}` , other : string ) : number { return 1; }
"""
    get = _content_provider({"contract.ts": old_src}, {"contract.ts": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


def test_python_pep695_signature_formatting_only_edit_is_excluded() -> None:
    old_src = "def contract[T: (str, tuple[int, bytes])](value:T=1)->T:\n    return value\n"
    new_src = (
        "def contract[ T : ( str , tuple [ int , bytes ] ) ]"
        "( value : T = 1 ) -> T:\n"
        "    return value\n"
    )
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1,2 +1,2 @@
-def contract[T: (str, tuple[int, bytes])](value:T=1)->T:
+def contract[ T : ( str , tuple [ int , bytes ] ) ]( value : T = 1 ) -> T:
     return value
"""
    get = _content_provider({"contract.py": old_src}, {"contract.py": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.files[0].changes == []


def test_python_pep695_default_removal_is_a_breaking_signature_change() -> None:
    old_src = "def contract[T: str](value: T = 1) -> T:\n    return value\n"
    new_src = "def contract[T: str](value: T) -> T:\n    return value\n"
    diff = """\
diff --git a/contract.py b/contract.py
--- a/contract.py
+++ b/contract.py
@@ -1,2 +1,2 @@
-def contract[T: str](value: T = 1) -> T:
+def contract[T: str](value: T) -> T:
     return value
"""
    get = _content_provider({"contract.py": old_src}, {"contract.py": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.category_id == "default_removed"
    assert change.breaking is True


def test_typescript_generic_function_type_change_keeps_outer_arity() -> None:
    old_src = "function contract(cb: <T, U>(x: T) => U, enabled: boolean): void { return; }\n"
    new_src = (
        "function contract(cb: <T, U>(x: T) => Promise<U>, enabled: boolean): void { return; }\n"
    )
    diff = """\
diff --git a/contract.ts b/contract.ts
--- a/contract.ts
+++ b/contract.ts
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({"contract.ts": old_src}, {"contract.ts": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.category_id == "parameters_changed"


def test_typescript_instantiation_expression_change_keeps_outer_arity() -> None:
    old_src = "function contract(cb = makePair<string>, enabled = true): void { return; }\n"
    new_src = "function contract(cb = makePair<string, number>, enabled = true): void { return; }\n"
    diff = """\
diff --git a/contract.ts b/contract.ts
--- a/contract.ts
+++ b/contract.ts
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({"contract.ts": old_src}, {"contract.ts": new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.category_id == "parameters_changed"
    assert change.breaking is None


@pytest.mark.parametrize(
    ("path", "old_heritage", "new_heritage"),
    [
        ("service.ts", "mixin(Base)", "mixin(Base, Trait)"),
        ("service.ts", "mixin(Base, Trait)", "mixin(Base)"),
        ("service.ts", "Base", "Replacement"),
        ("service.js", "mixin(Base)", "mixin(Base, Trait)"),
        ("service.js", "mixin(Base, Trait)", "mixin(Base)"),
        ("service.js", "Base", "Replacement"),
    ],
)
def test_class_heritage_changes_are_not_callable_parameter_changes(
    path: str,
    old_heritage: str,
    new_heritage: str,
) -> None:
    old_src = f"class Service extends {old_heritage} {{}}\n"
    new_src = f"class Service extends {new_heritage} {{}}\n"
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.category_id == "class_signature_changed"
    assert change.breaking is None


@pytest.mark.parametrize(
    ("path", "declaration"),
    [
        ("service.ts", "export class"),
        ("service.js", "export class"),
        ("service.ts", "export default class"),
        ("service.js", "export default class"),
        ("service.ts", "export abstract class"),
        ("service.ts", "export default abstract class"),
    ],
)
def test_exported_class_heritage_change_is_not_a_parameter_addition(
    path: str,
    declaration: str,
) -> None:
    old_src = f"{declaration} Service extends mixin(Base) {{}}\n"
    new_src = f"{declaration} Service extends mixin(Base, Trait) {{}}\n"
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.rule_id == "DG110"
    assert change.category_id == "class_signature_changed"
    assert change.breaking is None


@pytest.mark.parametrize(
    ("path", "old_src", "new_src"),
    [
        (
            "contract.ts",
            "function contract<T extends (x: Map<string, Array<number>>) => Promise<void>>"
            "(a: number, b: string): void { return; }\n",
            "function contract<T extends (x: Map<string, Array<number>>) => Promise<void>>"
            "(a: number, b: string, c: boolean): void { return; }\n",
        ),
        (
            "contract.go",
            "package sample\n"
            "func contract[T interface{ M(func(int, string)); N() }]"
            "(a int, b string) {}\n",
            "package sample\n"
            "func contract[T interface{ M(func(int, string)); N() }]"
            "(a int, b string, c bool) {}\n",
        ),
    ],
)
def test_generic_constraints_do_not_hide_outer_parameter_addition(
    path: str,
    old_src: str,
    new_src: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_src}, {path: new_src})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert len(result.files[0].changes) == 1
    change = result.files[0].changes[0]
    assert change.kind == "signature_changed"
    assert change.category_id == "parameter_added"
    assert change.breaking is None


def _split_recreation_diff(new_declaration: str) -> str:
    return f"""\
diff --git a/contract.py b/contract.py
deleted file mode 100644
--- a/contract.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def contract(value=1):
-    return value
diff --git a/notes.md b/notes.md
--- a/notes.md
+++ b/notes.md
@@ -1 +1 @@
-old
+new
diff --git a/scratch.txt b/scratch.txt
new file mode 100644
--- /dev/null
+++ b/scratch.txt
@@ -0,0 +1 @@
+scratch
diff --git a/contract.py b/contract.py
new file mode 100644
--- /dev/null
+++ b/contract.py
@@ -0,0 +1,2 @@
+{new_declaration}
+    return value
"""


def test_identical_nonadjacent_recreation_has_no_fabricated_removal() -> None:
    source = "def contract(value=1):\n    return value\n"
    get = _content_provider({"contract.py": source}, {"contract.py": source})

    result = run_pipeline(
        _split_recreation_diff("def contract(value=1):"),
        "abc..def",
        get,  # type: ignore[arg-type]
    )

    contract_files = [
        file_change for file_change in result.files if file_change.path == "contract.py"
    ]
    assert len(contract_files) == 1
    assert contract_files[0].change_type == "modified"
    assert contract_files[0].changes == []
    assert result.meta.stats.files == 3
    assert result.meta.warnings == []


def test_modified_nonadjacent_recreation_is_analyzed_as_one_file() -> None:
    old_source = "def contract(value=1):\n    return value\n"
    new_source = "def contract(value):\n    return value\n"
    get = _content_provider({"contract.py": old_source}, {"contract.py": new_source})

    result = run_pipeline(
        _split_recreation_diff("def contract(value):"),
        "abc..def",
        get,  # type: ignore[arg-type]
    )

    contract_files = [
        file_change for file_change in result.files if file_change.path == "contract.py"
    ]
    assert len(contract_files) == 1
    assert contract_files[0].change_type == "modified"
    assert [change.category_id for change in contract_files[0].changes] == ["default_removed"]
    assert not any(
        change.kind in {"function_removed", "function_added"}
        for change in contract_files[0].changes
    )
    assert result.meta.stats.files == 3


def test_empty_diff() -> None:
    result = run_pipeline("", "a..b")
    assert result.files == []
    assert result.tiered.oneliner != ""


def test_cross_language_replacement_is_not_reconciled_as_a_move() -> None:
    diff = """\
diff --git a/old.go b/old.go
deleted file mode 100644
--- a/old.go
+++ /dev/null
@@ -1,2 +0,0 @@
-package sample
-func helper() {}
diff --git a/new.js b/new.js
new file mode 100644
--- /dev/null
+++ b/new.js
@@ -0,0 +1 @@
+function helper() {}
"""
    get = _content_provider(
        {"old.go": "package sample\nfunc helper() {}\n"},
        {"new.js": "function helper() {}\n"},
    )

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    by_path = {file_change.path: file_change for file_change in result.files}
    assert [(change.kind, change.rule_id) for change in by_path["old.go"].changes] == [
        ("function_removed", "DG201")
    ]
    assert [change.kind for change in by_path["new.js"].changes] == ["function_added"]
    assert not any(
        change.rule_id == "DG202" for file_change in result.files for change in file_change.changes
    )


def test_same_language_cross_file_move_is_still_reconciled() -> None:
    diff = """\
diff --git a/old.go b/old.go
deleted file mode 100644
--- a/old.go
+++ /dev/null
@@ -1,2 +0,0 @@
-package sample
-func helper() {}
diff --git a/new.go b/new.go
new file mode 100644
--- /dev/null
+++ b/new.go
@@ -0,0 +1,2 @@
+package sample
+func helper() {}
"""
    source = "package sample\nfunc helper() {}\n"
    get = _content_provider({"old.go": source}, {"new.go": source})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    by_path = {file_change.path: file_change for file_change in result.files}
    assert by_path["old.go"].changes == []
    assert [(change.kind, change.rule_id) for change in by_path["new.go"].changes] == [
        ("moved", "DG202")
    ]
    assert by_path["new.go"].changes[0].file_from == "old.go"


def test_duplicate_path_records_are_not_partially_rewritten_as_a_move() -> None:
    declaration = "def helper(value):\n    return value\n"
    diff = """\
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def helper(value):
-    return value
diff --git a/old.py b/old.py
deleted file mode 100644
--- a/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def helper(value):
-    return value
diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,2 @@
+def helper(value):
+    return value
"""
    get = _content_provider(
        {"old.py": declaration},
        {"new.py": declaration},
    )

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    assert result.meta.stats.files == 3
    old_records = [file_change for file_change in result.files if file_change.path == "old.py"]
    new_record = next(file_change for file_change in result.files if file_change.path == "new.py")
    assert len(old_records) == 2
    assert [change.kind for record in old_records for change in record.changes] == [
        "function_removed",
        "function_removed",
    ]
    assert [change.kind for change in new_record.changes] == ["function_added"]


# ---------------------------------------------------------------------------
# Regression tests for _apply_moves
# ---------------------------------------------------------------------------


def _make_symbol(name: str, sig: str = "def f()") -> Symbol:
    body = f"body of {name}"
    return Symbol(
        name=name,
        kind="function",
        signature=sig,
        start_line=1,
        end_line=2,
        body_hash=compute_body_hash(body),
    )


def test_apply_moves_same_named_symbols_not_stripped() -> None:
    """Regression: if two files have a symbol with the same name, only the
    source file's added/removed entry should be stripped — not the other file's."""
    # Setup: file_a.py and file_c.py both have a symbol called "helper".
    # "helper" is moved from file_a.py -> file_b.py.
    # file_c.py's "helper" (function_added) must NOT be touched.
    fc_a = FileChange(
        path="file_a.py",
        language="python",
        change_type="modified",
        changes=[SymbolChange(kind="function_removed", name="helper")],
    )
    fc_b = FileChange(
        path="file_b.py",
        language="python",
        change_type="modified",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )
    fc_c = FileChange(
        path="file_c.py",
        language="python",
        change_type="modified",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )

    old_sym = _make_symbol("helper")
    new_sym = _make_symbol("helper")
    moves = [MatchedSymbol(old=old_sym, new=new_sym, file_from="file_a.py", file_to="file_b.py")]

    _apply_moves(moves, [fc_a, fc_b, fc_c])

    # file_c's "helper" must still be present
    assert any(c.name == "helper" and c.kind == "function_added" for c in fc_c.changes), (
        "Same-named symbol in unrelated file was incorrectly stripped"
    )
    # file_b should have the moved change, not the original function_added
    assert any(c.kind == "moved" and c.name == "helper" for c in fc_b.changes)
    assert not any(c.kind == "function_added" for c in fc_b.changes)


def test_apply_moves_destination_attribution() -> None:
    """Regression: move change must be added to the correct destination file,
    not the first file_change that happens to match."""
    fc_src = FileChange(
        path="old_module.py",
        language="python",
        change_type="modified",
        changes=[SymbolChange(kind="function_removed", name="do_work")],
    )
    fc_dst = FileChange(
        path="new_module.py",
        language="python",
        change_type="modified",
        changes=[SymbolChange(kind="function_added", name="do_work")],
    )
    fc_other = FileChange(
        path="other.py",
        language="python",
        change_type="modified",
        changes=[],
    )

    old_sym = _make_symbol("do_work")
    new_sym = _make_symbol("do_work")
    moves = [
        MatchedSymbol(old=old_sym, new=new_sym, file_from="old_module.py", file_to="new_module.py")
    ]

    _apply_moves(moves, [fc_other, fc_src, fc_dst])

    # Move should be on fc_dst, not fc_other or fc_src
    assert any(c.kind == "moved" for c in fc_dst.changes), (
        "Move change not attributed to destination file"
    )
    assert not any(c.kind == "moved" for c in fc_src.changes), (
        "Move change incorrectly on source file"
    )
    assert not any(c.kind == "moved" for c in fc_other.changes), (
        "Move change incorrectly on unrelated file"
    )


def test_apply_moves_leaves_duplicate_path_records_unchanged() -> None:
    first_source = FileChange(
        path="old.py",
        language="python",
        change_type="removed",
        changes=[SymbolChange(kind="function_removed", name="helper")],
    )
    second_source = FileChange(
        path="old.py",
        language="python",
        change_type="removed",
        changes=[SymbolChange(kind="function_removed", name="helper")],
    )
    destination = FileChange(
        path="new.py",
        language="python",
        change_type="added",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )
    old_symbol = _make_symbol("helper")
    new_symbol = _make_symbol("helper")

    _apply_moves(
        [MatchedSymbol(old_symbol, new_symbol, file_from="old.py", file_to="new.py")],
        [first_source, second_source, destination],
    )

    assert [change.kind for change in first_source.changes] == ["function_removed"]
    assert [change.kind for change in second_source.changes] == ["function_removed"]
    assert [change.kind for change in destination.changes] == ["function_added"]


def test_apply_moves_rejects_a_cross_language_candidate() -> None:
    source = FileChange(
        path="old.go",
        language="go",
        change_type="removed",
        changes=[SymbolChange(kind="function_removed", name="helper")],
    )
    destination = FileChange(
        path="new.js",
        language="javascript",
        change_type="added",
        changes=[SymbolChange(kind="function_added", name="helper")],
    )

    _apply_moves(
        [
            MatchedSymbol(
                _make_symbol("helper", "func helper()"),
                _make_symbol("helper", "function helper()"),
                file_from="old.go",
                file_to="new.js",
            )
        ],
        [source, destination],
    )

    assert [change.kind for change in source.changes] == ["function_removed"]
    assert [change.kind for change in destination.changes] == ["function_added"]


def test_move_preserves_simultaneous_signature_change() -> None:
    fc_src = FileChange(
        path="old.py",
        language="python",
        change_type="removed",
        changes=[SymbolChange(kind="function_removed", name="f")],
    )
    fc_dst = FileChange(
        path="new.py",
        language="python",
        change_type="added",
        changes=[SymbolChange(kind="function_added", name="f")],
    )
    old_symbol = _make_symbol("f", "def f(x=1)")
    new_symbol = _make_symbol("f", "def f(x)")

    _apply_moves(
        [MatchedSymbol(old_symbol, new_symbol, file_from="old.py", file_to="new.py")],
        [fc_src, fc_dst],
    )

    assert [change.kind for change in fc_dst.changes] == ["moved", "signature_changed"]
    assert fc_dst.changes[1].category_id == "default_removed"
    assert fc_dst.changes[1].breaking is True


def test_same_named_moves_keep_one_to_one_paths() -> None:
    file_changes: list[FileChange] = []
    moves: list[MatchedSymbol] = []
    for index in (1, 2):
        old_path = f"old{index}.py"
        new_path = f"new{index}.py"
        old_symbol = _make_symbol("f", f"def f(x={index})")
        new_symbol = _make_symbol("f", f"def f(x={index})")
        file_changes.extend(
            [
                FileChange(
                    path=old_path,
                    language="python",
                    change_type="removed",
                    changes=[SymbolChange(kind="function_removed", name="f")],
                ),
                FileChange(
                    path=new_path,
                    language="python",
                    change_type="added",
                    changes=[SymbolChange(kind="function_added", name="f")],
                ),
            ]
        )
        moves.append(MatchedSymbol(old_symbol, new_symbol, file_from=old_path, file_to=new_path))

    _apply_moves(moves, file_changes)

    by_path = {change.path: change for change in file_changes}
    for index in (1, 2):
        assert by_path[f"old{index}.py"].changes == []
        destination_changes = by_path[f"new{index}.py"].changes
        assert len(destination_changes) == 1
        assert destination_changes[0].kind == "moved"
        assert destination_changes[0].file_from == f"old{index}.py"


def test_move_reconciliation_preserves_same_named_duplicate_removal() -> None:
    moved_old = Symbol(
        name="f",
        kind="function",
        signature="def f(a)",
        start_line=1,
        end_line=2,
        body_hash=compute_body_hash("return a"),
    )
    moved_new = Symbol(
        name="f",
        kind="function",
        signature="def f(a)",
        start_line=1,
        end_line=2,
        body_hash=compute_body_hash("return a"),
    )
    src = FileChange(
        path="old.py",
        language="python",
        change_type="removed",
        changes=[
            SymbolChange(
                kind="function_removed",
                name="f",
                signature="def f(a)",
                line=1,
            ),
            SymbolChange(
                kind="function_removed",
                name="f",
                signature="def f(b)",
                line=4,
            ),
        ],
    )
    dst = FileChange(
        path="new.py",
        language="python",
        change_type="added",
        changes=[
            SymbolChange(
                kind="function_added",
                name="f",
                signature="def f(a)",
                line=1,
            )
        ],
    )

    _apply_moves(
        [MatchedSymbol(moved_old, moved_new, file_from="old.py", file_to="new.py")],
        [src, dst],
    )

    assert [(change.signature, change.line) for change in src.changes] == [("def f(b)", 4)]
    assert [change.kind for change in dst.changes] == ["moved"]


@pytest.mark.parametrize(
    ("path", "language", "old_source", "new_source", "symbol"),
    [
        (
            "contract.py",
            "python",
            "def contract(value):\n    return value\n",
            "async def contract(value):\n    return value\n",
            "contract",
        ),
        (
            "contract.ts",
            "typescript",
            "class Contract { run(value: number) { return value; } }\n",
            "class Contract { private static async run(value: number) { return value; } }\n",
            "run",
        ),
    ],
)
def test_callable_modifier_change_surfaces_as_signature_finding(
    path: str,
    language: str,
    old_source: str,
    new_source: str,
    symbol: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_source}, {path: new_source})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    changes = [
        change
        for file_change in result.files
        for change in file_change.changes
        if change.name == symbol and change.kind == "signature_changed"
    ]
    assert len(changes) == 1
    assert changes[0].breaking is None
    assert result.files[0].language == language


@pytest.mark.parametrize(
    ("path", "old_source", "new_source", "symbol"),
    [
        (
            "contract.py",
            "def contract[T](value: T) -> T:\n    return value\n",
            "def contract[T: str](value: T) -> T:\n    return value\n",
            "contract",
        ),
        (
            "contract.ts",
            "function contract<T>(value: T): T { return value; }\n",
            "function contract<T extends string>(value: T): T { return value; }\n",
            "contract",
        ),
        (
            "contract.go",
            "package p\nfunc Contract[T any](value T) T { return value }\n",
            "package p\nfunc Contract[T ~string](value T) T { return value }\n",
            "Contract",
        ),
    ],
)
def test_type_parameter_change_surfaces_as_signature_finding(
    path: str,
    old_source: str,
    new_source: str,
    symbol: str,
) -> None:
    diff = f"""\
diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -1 +1 @@
-old
+new
"""
    get = _content_provider({path: old_source}, {path: new_source})

    result = run_pipeline(diff, "abc..def", get)  # type: ignore[arg-type]

    changes = [
        change
        for file_change in result.files
        for change in file_change.changes
        if change.name == symbol and change.kind == "signature_changed"
    ]
    assert len(changes) == 1
    assert changes[0].before_signature != changes[0].after_signature
    assert changes[0].breaking is None
