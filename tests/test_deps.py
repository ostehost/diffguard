"""Tests for diffguard.engine.deps — dependency reference scanning."""

from __future__ import annotations


from diffguard.engine.deps import (
    _candidate_files,
    _scan_file_for_symbols,
    find_references,
    scan_references,
)


def _init_git_repo(repo: str, *, email: str = "t@t.com", name: str = "T") -> None:
    """Initialize a hermetic temp repo for tests.

    Test repositories must not inherit the operator's global git hooks; otherwise
    local commit-msg policy can make otherwise portable tests fail.
    """

    import subprocess

    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", email], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(["git", "config", "user.name", name], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "core.hooksPath", ""], cwd=repo, capture_output=True, check=True
    )


class TestScanFileForSymbols:
    """Unit tests for _scan_file_for_symbols."""

    def test_finds_identifier_in_python(self):
        source = "from foo import bar\nresult = bar(42)\n"
        hits = _scan_file_for_symbols(source, "python", {"bar"})
        assert len(hits) == 2
        names = [h[0] for h in hits]
        assert all(n == "bar" for n in names)

    def test_import_context_detected(self):
        source = "from foo import bar\n"
        hits = _scan_file_for_symbols(source, "python", {"bar"})
        assert len(hits) == 1
        assert hits[0][2] == "import"

    def test_call_context_detected(self):
        source = "x = process_request(environ)\n"
        hits = _scan_file_for_symbols(source, "python", {"process_request"})
        assert len(hits) == 1
        assert hits[0][2] == "call"

    def test_transparent_wrappers_preserve_call_context(self) -> None:
        cases: dict[str, tuple[str, int]] = {
            "python": ("(target)()\n((target))()\n(# before\n target)()\n", 3),
            "javascript": ("(target)();\n((target))();\n(/* before */ target)();\n", 3),
            "typescript": (
                (
                    "(target)();\n"
                    "target!();\n"
                    "(target as Fn)();\n"
                    "(<Fn>target)();\n"
                    "(target satisfies Fn)();\n"
                    "((target!) as Fn)();\n"
                    "(/* before */ target as Fn)();\n"
                ),
                7,
            ),
        }

        for language, (source, expected_calls) in cases.items():
            hits = _scan_file_for_symbols(source, language, {"target"})

            assert [context for _, _, context, _ in hits] == ["call"] * expected_calls

    def test_wrapped_calls_only_promote_runtime_expression_operands(self) -> None:
        source = (
            "(target || other)();\n"
            "(target as TargetType)();\n"
            "(<AssertedType>target)();\n"
            "(target satisfies Constraint)();\n"
            "(target as Callable).method();\n"
        )

        hits = _scan_file_for_symbols(
            source,
            "typescript",
            {"target", "other", "TargetType", "AssertedType", "Constraint", "Callable"},
        )

        assert [(name, line, context) for name, line, context, _ in hits] == [
            ("target", 1, "reference"),
            ("other", 1, "reference"),
            ("target", 2, "call"),
            ("TargetType", 2, "reference"),
            ("AssertedType", 3, "reference"),
            ("target", 3, "call"),
            ("target", 4, "call"),
            ("Constraint", 4, "reference"),
            ("target", 5, "reference"),
            ("Callable", 5, "reference"),
        ]

    def test_nontransparent_callable_expressions_remain_references(self) -> None:
        cases: dict[str, tuple[str, int]] = {
            "python": (
                (
                    "async def audit():\n"
                    "    (await target)()\n"
                    "    (target[index])()\n"
                    "    (target if condition else other)()\n"
                    "    (other, target)()\n"
                ),
                4,
            ),
            "javascript": (
                "async function audit() {\n"
                "  (await target)();\n"
                "  (target[index])();\n"
                "  (condition ? target : other)();\n"
                "  (other, target)();\n"
                "  new target();\n"
                "}\n",
                5,
            ),
            "typescript": (
                "async function audit() {\n"
                "  (await target)();\n"
                "  (target[index])();\n"
                "  (condition ? target : other)();\n"
                "  (other, target)();\n"
                "  new target();\n"
                "}\n",
                5,
            ),
        }

        for language, (source, expected_references) in cases.items():
            hits = _scan_file_for_symbols(source, language, {"target"})

            assert [context for _, _, context, _ in hits] == ["reference"] * expected_references

    def test_no_match_returns_empty(self):
        source = "x = 42\n"
        hits = _scan_file_for_symbols(source, "python", {"nonexistent"})
        assert hits == []

    def test_typescript_identifiers(self):
        source = "import { foo } from './bar';\nfoo();\n"
        hits = _scan_file_for_symbols(source, "typescript", {"foo"})
        assert len(hits) >= 2

    def test_tsx_references_use_path_specific_grammar(self, tmp_path):
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "component.tsx").write_text(
            "export function Widget() { return <button onClick={target}>Run</button>; }\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        scan = scan_references(repo, ["target"], "HEAD")

        assert [(reference.line, reference.context) for reference in scan.references] == [
            (1, "reference")
        ]
        assert scan.warnings == []

    def test_typescript_type_identifiers_are_references(self):
        source = "let value: Target;\nfunction f(arg: Target): Target { return arg as Target; }\n"

        hits = _scan_file_for_symbols(source, "typescript", {"Target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (1, "reference"),
            (2, "reference"),
            (2, "reference"),
            (2, "reference"),
        ]

    def test_go_type_identifiers_are_references(self):
        source = "package p\nvar value Target\nfunc f(arg Target) Target { return arg }\n"

        hits = _scan_file_for_symbols(source, "go", {"Target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (2, "reference"),
            (3, "reference"),
            (3, "reference"),
        ]

    def test_python_import_alias_bindings_are_not_references(self):
        source = (
            "import package as target\n"
            "from module import original as target\n"
            "from module import target as local\n"
            "value = target\n"
        )

        hits = _scan_file_for_symbols(source, "python", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (3, "import"),
            (4, "reference"),
        ]

    def test_typescript_import_alias_bindings_are_not_references(self):
        source = (
            "import {Original as target, target as local} from './module';\nconst value = target;\n"
        )

        hits = _scan_file_for_symbols(source, "typescript", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (1, "import"),
            (2, "reference"),
        ]

    def test_python_keyword_labels_are_not_references_but_values_are(self):
        source = "result = call(target=target, other=target())\n"

        hits = _scan_file_for_symbols(source, "python", {"target"})

        assert [context for _, _, context, _ in hits] == ["reference", "call"]

    def test_python_dictionary_keys_and_values_are_evaluated_references(self):
        source = "mapping = {target: target, make_key(): target}\n"

        hits = _scan_file_for_symbols(source, "python", {"target", "make_key"})

        assert [(name, context) for name, _, context, _ in hits] == [
            ("target", "reference"),
            ("target", "reference"),
            ("make_key", "call"),
            ("target", "reference"),
        ]

    def test_multiple_symbols(self):
        source = "import a\nimport b\na()\nb()\n"
        hits = _scan_file_for_symbols(source, "python", {"a", "b"})
        assert len(hits) == 4

    def test_line_numbers_correct(self):
        source = "x = 1\ny = 2\nfoo()\n"
        hits = _scan_file_for_symbols(source, "python", {"foo"})
        assert len(hits) == 1
        assert hits[0][1] == 3  # line 3

    def test_declarations_excluded_and_non_calls_labeled_reference(self):
        source = "def target():\n    pass\nvalue = target\nobj.target\ntarget()\n"
        hits = _scan_file_for_symbols(source, "python", {"target"})
        assert [(line, context) for _, line, context, _ in hits] == [
            (3, "reference"),
            (4, "reference"),
            (5, "call"),
        ]

    def test_member_call_is_call_but_member_access_is_reference(self):
        source = "obj.target;\nobj.target();\n"
        hits = _scan_file_for_symbols(source, "typescript", {"target"})
        assert [context for _, _, context, _ in hits] == ["reference", "call"]

    def test_python_parameters_and_loop_bindings_are_not_references(self):
        source = (
            "def f(target, other: target = target()):\n"
            "    for target in items:\n"
            "        pass\n"
            "    return target.method()\n"
            "[target for target in items]\n"
        )

        hits = _scan_file_for_symbols(source, "python", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (1, "reference"),
            (1, "call"),
            (4, "reference"),
            (5, "reference"),
        ]

    def test_python_lambda_parameters_are_not_references_but_expressions_are(self):
        source = "callback = lambda target, other=target(): target() + consume(target)\n"

        hits = _scan_file_for_symbols(source, "python", {"target"})

        assert [context for _, _, context, _ in hits] == ["call", "call", "reference"]

    def test_python_match_captures_are_not_references(self):
        source = (
            "match subject:\n"
            "    case target:\n"
            "        use(target)\n"
            '    case {"key": captured, **rest}:\n'
            "        use(captured, rest)\n"
        )

        hits = _scan_file_for_symbols(source, "python", {"target", "captured", "rest"})

        assert [(name, line, context) for name, line, context, _ in hits] == [
            ("target", 3, "reference"),
            ("captured", 5, "reference"),
            ("rest", 5, "reference"),
        ]

    def test_python_match_class_and_value_patterns_remain_references(self):
        source = (
            "match subject:\n"
            "    case Point(target, color=Color.RED, alias=other) as whole:\n"
            "        use(Point, target, Color, RED, other, whole)\n"
        )

        hits = _scan_file_for_symbols(
            source,
            "python",
            {"Point", "target", "color", "Color", "RED", "other", "whole"},
        )

        assert [(name, line, context) for name, line, context, _ in hits] == [
            ("Point", 2, "reference"),
            ("Color", 2, "reference"),
            ("RED", 2, "reference"),
            ("Point", 3, "reference"),
            ("target", 3, "reference"),
            ("Color", 3, "reference"),
            ("RED", 3, "reference"),
            ("other", 3, "reference"),
            ("whole", 3, "reference"),
        ]

    def test_python_alias_and_destructuring_bindings_are_not_references(self):
        source = (
            "with context() as target:\n"
            "    pass\n"
            "try:\n"
            "    pass\n"
            "except Error as target:\n"
            "    pass\n"
            "(target, other) = values\n"
        )

        assert _scan_file_for_symbols(source, "python", {"target"}) == []

    def test_python_unparenthesized_destructuring_bindings_are_not_references(self):
        source = (
            "first, target = target()\n"
            "for first, target in target:\n"
            "    use(target)\n"
            "for first, *target in rows:\n"
            "    use(target)\n"
            "values = [target for first, target in rows]\n"
            "from module import target\n"
        )

        hits = _scan_file_for_symbols(source, "python", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (1, "call"),
            (2, "reference"),
            (3, "reference"),
            (5, "reference"),
            (6, "reference"),
            (7, "import"),
        ]

    def test_typescript_bindings_and_call_receiver_roles(self):
        source = (
            "function f(target: T) {\n"
            "  for (const target of items) target.method();\n"
            "  const {value: target} = obj;\n"
            "  obj.target();\n"
            "}\n"
        )

        hits = _scan_file_for_symbols(source, "typescript", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (2, "reference"),
            (4, "call"),
        ]

    def test_typescript_and_javascript_catch_bindings_are_not_references(self):
        source = "try { run(); } catch (target) { consume(target); }\n"

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"target"})

            assert [(line, context) for _, line, context, _ in hits] == [(1, "reference")]

    def test_javascript_class_field_declarations_are_not_references(self):
        source = "class Example { target = target; }\nconsume(target);\n"

        hits = _scan_file_for_symbols(source, "javascript", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (1, "reference"),
            (2, "reference"),
        ]

    def test_typescript_and_javascript_private_method_calls_are_references(self) -> None:
        source = "class Example { #target() {} run() { this.#target(); } }\n"

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"#target"})

            assert [(line, context) for _, line, context, _ in hits] == [(1, "call")]

    def test_typescript_and_javascript_object_labels_are_not_value_references(self):
        source = (
            "const object = {target: target, [target]: target, target};\n"
            "const {field: target} = source;\n"
        )

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"target"})

            assert [(line, context) for _, line, context, _ in hits] == [
                (1, "reference"),
                (1, "reference"),
                (1, "reference"),
                (1, "reference"),
            ]

    def test_typescript_and_javascript_object_pattern_defaults_are_references(self):
        source = "const {value = target} = source;\n"

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"value", "target"})

            assert [(name, line, context) for name, line, context, _ in hits] == [
                ("target", 1, "reference")
            ]

    def test_typescript_and_javascript_parameter_defaults_are_references(self):
        source = "function f({value = target}) {}\n"

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"value", "target"})

            assert [(name, line, context) for name, line, context, _ in hits] == [
                ("target", 1, "reference")
            ]

    def test_typescript_and_javascript_catch_destructuring_default_bindings(self):
        source = "try{}catch({x: target = source}){use(target)}"

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"target", "source"})

            assert [(name, context) for name, _, context, _ in hits] == [
                ("source", "reference"),
                ("target", "reference"),
            ]

    def test_typescript_and_javascript_declaration_destructuring_default_bindings(self):
        source = (
            "const {x: target = source} = input;\n"
            "function f({x: target = source}) { use(target); }\n"
        )

        for language in ("typescript", "javascript"):
            hits = _scan_file_for_symbols(source, language, {"target", "source"})

            assert [(name, line, context) for name, line, context, _ in hits] == [
                ("source", 1, "reference"),
                ("source", 2, "reference"),
                ("target", 2, "reference"),
            ]

    def test_go_bindings_and_call_receiver_roles(self):
        source = (
            "package p\n"
            "func f(target T) {\n"
            "  for target := range items { target.Method() }\n"
            "  obj.target()\n"
            "}\n"
        )

        hits = _scan_file_for_symbols(source, "go", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (3, "reference"),
            (4, "call"),
        ]

    def test_go_map_keys_are_references_but_struct_field_labels_are_not(self):
        source = (
            "package p\n"
            "var mapping = map[string]int{target: target}\n"
            "var record = Record{target: target}\n"
            "var inline = struct{ target int }{target: target}\n"
        )

        hits = _scan_file_for_symbols(source, "go", {"target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (2, "reference"),
            (2, "reference"),
            (3, "reference"),
            (4, "reference"),
        ]

    def test_typescript_declaration_names_and_property_labels_are_not_references(self):
        source = (
            "type Target = string;\n"
            "interface TargetFace { Target: string; }\n"
            "enum TargetEnum { Target = 1 }\n"
            "namespace TargetNamespace {}\n"
            "abstract class TargetClass {\n"
            "  abstract Target?(): void;\n"
            "  Target = 1;\n"
            "}\n"
            "function* TargetGenerator() {}\n"
            "const value = {Target: Target};\n"
        )

        hits = _scan_file_for_symbols(
            source,
            "typescript",
            {
                "Target",
                "TargetFace",
                "TargetEnum",
                "TargetNamespace",
                "TargetClass",
                "TargetGenerator",
            },
        )

        assert [(name, line, context) for name, line, context, _ in hits] == [
            ("Target", 10, "reference")
        ]

    def test_typescript_bare_enum_member_is_not_a_reference_but_uses_are(self):
        source = "enum E { Target }\nconst direct = Target;\nconst qualified = E.Target;\n"

        hits = _scan_file_for_symbols(source, "typescript", {"Target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (2, "reference"),
            (3, "reference"),
        ]

    def test_go_declaration_and_assignment_names_are_not_references(self):
        source = (
            "package p\n"
            "type Target[T any] struct { Target int }\n"
            "var TargetVar int\n"
            "const TargetConst = 1\n"
            "func TargetFunc(TargetParam int) {\n"
            "  TargetLocal := 1\n"
            "  TargetLocal = TargetVar\n"
            "  _ = Holder{TargetField: TargetValue}\n"
            "}\n"
        )

        hits = _scan_file_for_symbols(
            source,
            "go",
            {
                "Target",
                "TargetVar",
                "TargetConst",
                "TargetFunc",
                "TargetParam",
                "TargetLocal",
                "TargetField",
                "TargetValue",
                "T",
            },
        )

        assert [(name, line, context) for name, line, context, _ in hits] == [
            ("TargetVar", 7, "reference"),
            ("TargetValue", 8, "reference"),
        ]

    def test_go_interface_method_name_is_not_a_reference_but_calls_are(self):
        source = (
            "package p\n"
            "type I interface { Target(int) error }\n"
            "func call(i I) error { return i.Target(1) }\n"
            "func direct() { Target(1) }\n"
        )

        hits = _scan_file_for_symbols(source, "go", {"Target"})

        assert [(line, context) for _, line, context, _ in hits] == [
            (3, "call"),
            (4, "call"),
        ]


class TestFindReferencesIntegration:
    """Integration tests using the actual diffguard repo."""

    def test_finds_refs_in_own_repo(self, tmp_path):
        """Test that find_references works on a real git repo."""
        import subprocess

        # Create a small test repo
        repo = str(tmp_path)
        _init_git_repo(repo, email="test@test.com", name="Test")

        # Create files
        (tmp_path / "lib.py").write_text("def helper():\n    return 42\n")
        (tmp_path / "main.py").write_text("from lib import helper\nhelper()\n")
        (tmp_path / "other.py").write_text("x = 1\n")

        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        refs = find_references(
            repo_path=repo,
            changed_symbols=["helper"],
            ref="HEAD",
            changed_files={"lib.py"},
        )

        assert len(refs) == 2  # import + call in main.py
        assert all(r.file_path == "main.py" for r in refs)
        assert any(r.context == "import" for r in refs)
        assert any(r.context == "call" for r in refs)

    def test_includes_changed_files_but_excludes_declarations(self, tmp_path):
        """Changed files retain useful references without counting declarations."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)

        (tmp_path / "a.py").write_text("def foo(): pass\nfoo()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        refs = find_references(
            repo_path=repo,
            changed_symbols=["foo"],
            ref="HEAD",
            changed_files={"a.py"},
        )
        assert len(refs) == 1
        assert refs[0].context == "call"
        assert refs[0].line == 2

    def test_changed_file_scan_excludes_unparenthesized_binding_targets(self, tmp_path):
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)

        (tmp_path / "a.py").write_text(
            "def target():\n"
            "    return 1\n"
            "value = target()\n"
            "first, target = values\n"
            "for first, target in values:\n"
            "    pass\n"
        )
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        scan = scan_references(
            repo_path=repo,
            changed_symbols=["target"],
            ref="HEAD",
            changed_files={"a.py"},
        )

        assert [(ref.line, ref.context) for ref in scan.references] == [(3, "call")]
        assert scan.warnings == []

    def test_empty_symbols_returns_empty(self, tmp_path):
        refs = find_references(
            repo_path=str(tmp_path),
            changed_symbols=[],
            ref="HEAD",
            changed_files=set(),
        )
        assert refs == []

    def test_same_named_modules_are_unresolved_not_exact(self, tmp_path):
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "a.py").write_text("def helper(): pass\n")
        (tmp_path / "b.py").write_text("def helper(): pass\n")
        (tmp_path / "use.py").write_text("from b import helper\nhelper()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        refs = find_references(repo, ["helper"], "HEAD", {"a.py"})

        assert refs
        assert all(ref.confidence == "low" for ref in refs)
        assert all("ownership unresolved" in ref.evidence for ref in refs)

    def test_reference_parse_gap_warns_without_emitting_hits(self, tmp_path):
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "broken.py").write_text("target(\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        scan = scan_references(repo, ["target"], "HEAD")

        assert scan.references == []
        assert scan.warnings == ["broken.py: reference scan has a parse gap"]


class TestGitGrepPreFilter:
    """Tests for git grep pre-filter."""

    def test_git_grep_finds_files(self, tmp_path):
        """git grep should find files containing the symbol."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)

        (tmp_path / "a.py").write_text("def helper(): pass\n")
        (tmp_path / "b.py").write_text("helper()\n")
        (tmp_path / "c.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        files = _candidate_files({"helper"}, "HEAD", repo)
        assert "a.py" in files
        assert "b.py" in files
        assert "c.py" not in files

    def test_git_grep_reduces_scan(self, tmp_path):
        """Pre-filter should reduce files scanned vs scanning all."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)

        # Create many files, only 1 references the symbol
        for i in range(20):
            (tmp_path / f"file_{i}.py").write_text(f"x_{i} = {i}\n")
        (tmp_path / "caller.py").write_text("from lib import target_func\ntarget_func()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        files = _candidate_files({"target_func"}, "HEAD", repo)
        assert len(files) == 1
        assert "caller.py" in files

    def test_typescript_dollar_identifier_is_matched_as_a_fixed_string(self, tmp_path):
        """Regex metacharacters in legal identifiers must remain literal."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "handler.ts").write_text(
            "export function handler$() {}\nhandler$();\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        scan = scan_references(repo, ["handler$"], "HEAD")

        assert [(ref.file_path, ref.line, ref.context) for ref in scan.references] == [
            ("handler.ts", 2, "call")
        ]
        assert scan.warnings == []

    def test_zero_matches_do_not_scan_unrelated_malformed_files(self, tmp_path):
        """A definitive no-match result should be an empty candidate set."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "broken.py").write_text("def broken(:\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        scan = scan_references(repo, ["missing_symbol"], "HEAD")

        assert scan.references == []
        assert scan.warnings == []

    def test_unreadable_candidates_warn_in_deterministic_deduplicated_order(self, monkeypatch):
        monkeypatch.setattr(
            "diffguard.engine.deps.grep_files",
            lambda *_args, **_kwargs: ["z.py", "a.ts", "z.py"],
        )
        monkeypatch.setattr(
            "diffguard.engine.deps.get_file_at_snapshot",
            lambda *_args, **_kwargs: None,
        )

        scan = scan_references("/repo", ["target", "other"], "snapshot")

        assert scan.references == []
        assert scan.warnings == [
            "a.ts: reference candidate at snapshot snapshot is unreadable — "
            "reference analysis incomplete",
            "z.py: reference candidate at snapshot snapshot is unreadable — "
            "reference analysis incomplete",
        ]

    def test_unavailable_fallback_listing_warns_without_fabricating_references(self, monkeypatch):
        monkeypatch.setattr("diffguard.engine.deps.grep_files", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            "diffguard.engine.deps.list_files_at_snapshot_with_status",
            lambda *_args, **_kwargs: ([], False),
        )

        scan = scan_references("/repo", ["target"], "missing")

        assert scan.references == []
        assert scan.warnings == [
            "reference file listing unavailable at snapshot missing — reference analysis incomplete"
        ]

    def test_unreadable_file_in_successful_fallback_warns(self, monkeypatch):
        monkeypatch.setattr("diffguard.engine.deps.grep_files", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            "diffguard.engine.deps.list_files_at_snapshot_with_status",
            lambda *_args, **_kwargs: (["bad.py"], True),
        )
        monkeypatch.setattr(
            "diffguard.engine.deps.get_file_at_snapshot",
            lambda *_args, **_kwargs: None,
        )

        scan = scan_references("/repo", ["target"], ":index")

        assert scan.references == []
        assert scan.warnings == [
            "bad.py: reference candidate at snapshot :index is unreadable — "
            "reference analysis incomplete"
        ]

    def test_invalid_utf8_candidate_warns_across_snapshot_kinds(self, tmp_path):
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "bad.py").write_bytes(b"target()\n\xff\n")
        (tmp_path / "good.py").write_text("target()\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)

        for ref in ("HEAD", ":index", ":worktree"):
            scan = scan_references(repo, ["target"], ref)

            assert [(item.file_path, item.line, item.context) for item in scan.references] == [
                ("good.py", 1, "call")
            ]
            assert scan.warnings == [
                f"bad.py: reference candidate at snapshot {ref} is unreadable — "
                "reference analysis incomplete"
            ]

    def test_unavailable_candidate_discovery_falls_back_to_supported_files(
        self, tmp_path, monkeypatch
    ):
        """An unavailable grep should retain references via the full scan."""
        import subprocess

        repo = str(tmp_path)
        _init_git_repo(repo)
        (tmp_path / "caller.py").write_text("target_func()\n")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True)
        monkeypatch.setattr("diffguard.engine.deps.grep_files", lambda *_args, **_kwargs: None)

        scan = scan_references(repo, ["target_func"], "HEAD")

        assert [(ref.file_path, ref.line, ref.context) for ref in scan.references] == [
            ("caller.py", 1, "call")
        ]
        assert scan.warnings == []
