"""Tests for git diff parsing and file retrieval."""

from __future__ import annotations

from diffguard.git import (
    is_generated,
    parse_diff,
)


# --- Fixtures: synthetic unified diffs ---

SIMPLE_MODIFY_DIFF = """\
diff --git a/src/app.py b/src/app.py
index abc1234..def5678 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,7 +10,7 @@ def hello():
     x = 1
     y = 2
     z = 3
-    return x + y
+    return x + y + z
     # trailing context
     pass
     end
"""

NEW_FILE_DIFF = """\
diff --git a/src/new_module.py b/src/new_module.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/new_module.py
@@ -0,0 +1,5 @@
+# New module.
+
+
+def greet(name: str) -> str:
+    return f"Hello, {name}"
"""

DELETED_FILE_DIFF = """\
diff --git a/src/old_module.py b/src/old_module.py
deleted file mode 100644
index abc1234..0000000
--- a/src/old_module.py
+++ /dev/null
@@ -1,3 +0,0 @@
-# Old module.
-
-OLD_CONST = 42
"""

BINARY_DIFF = """\
diff --git a/assets/logo.png b/assets/logo.png
index abc1234..def5678 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""

MULTI_HUNK_DIFF = """\
diff --git a/src/utils.py b/src/utils.py
index abc1234..def5678 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -5,4 +5,4 @@ def foo():
     a = 1
     b = 2
     c = 3
-    return a
+    return a + b
@@ -20,3 +20,4 @@ def bar():
     x = 10
     y = 20
     z = 30
+    return x + y + z
"""

MULTI_FILE_DIFF = SIMPLE_MODIFY_DIFF + NEW_FILE_DIFF + DELETED_FILE_DIFF

GENERATED_FILE_DIFF = """\
diff --git a/package-lock.json b/package-lock.json
index abc1234..def5678 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
 {
-  "version": "1.0.0"
+  "version": "1.1.0"
 }
"""

VENDORED_DIFF = """\
diff --git a/vendor/lib/thing.go b/vendor/lib/thing.go
index abc1234..def5678 100644
--- a/vendor/lib/thing.go
+++ b/vendor/lib/thing.go
@@ -1,3 +1,3 @@
 package thing
-var X = 1
+var X = 2
"""

MINIFIED_DIFF = """\
diff --git a/dist/bundle.min.js b/dist/bundle.min.js
index abc1234..def5678 100644
--- a/dist/bundle.min.js
+++ b/dist/bundle.min.js
@@ -1 +1 @@
-var a=1;
+var a=2;
"""


class TestParseDiff:
    def test_simple_modification(self) -> None:
        files = parse_diff(SIMPLE_MODIFY_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.old_path == "src/app.py"
        assert f.new_path == "src/app.py"
        assert f.change_type == "modified"
        assert not f.binary
        assert not f.generated
        assert len(f.hunks) == 1
        assert f.additions == 1
        assert f.deletions == 1

    def test_hunk_header_parsing(self) -> None:
        files = parse_diff(SIMPLE_MODIFY_DIFF)
        hdr = files[0].hunks[0].header
        assert hdr.old_start == 10
        assert hdr.old_count == 7
        assert hdr.new_start == 10
        assert hdr.new_count == 7
        assert hdr.section == "def hello():"

    def test_line_numbers(self) -> None:
        files = parse_diff(SIMPLE_MODIFY_DIFF)
        hunk = files[0].hunks[0]
        # Find the removed line
        removed = [ln for ln in hunk.lines if ln.origin == "-"]
        assert len(removed) == 1
        assert removed[0].old_lineno == 13
        assert removed[0].new_lineno is None
        # Find the added line
        added = [ln for ln in hunk.lines if ln.origin == "+"]
        assert len(added) == 1
        assert added[0].new_lineno == 13
        assert added[0].old_lineno is None

    def test_new_file(self) -> None:
        files = parse_diff(NEW_FILE_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.old_path is None
        assert f.new_path == "src/new_module.py"
        assert f.change_type == "added"
        assert f.additions == 5
        assert f.deletions == 0

    def test_deleted_file(self) -> None:
        files = parse_diff(DELETED_FILE_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.old_path == "src/old_module.py"
        assert f.new_path is None
        assert f.change_type == "removed"
        assert f.additions == 0
        assert f.deletions == 3

    def test_binary_file(self) -> None:
        files = parse_diff(BINARY_DIFF)
        assert len(files) == 1
        f = files[0]
        assert f.binary
        assert f.change_type == "modified"
        assert len(f.hunks) == 0

    def test_multi_hunk(self) -> None:
        files = parse_diff(MULTI_HUNK_DIFF)
        assert len(files) == 1
        f = files[0]
        assert len(f.hunks) == 2
        assert f.additions == 2
        assert f.deletions == 1

    def test_multi_file(self) -> None:
        files = parse_diff(MULTI_FILE_DIFF)
        assert len(files) == 3
        assert files[0].change_type == "modified"
        assert files[1].change_type == "added"
        assert files[2].change_type == "removed"

    def test_empty_diff(self) -> None:
        files = parse_diff("")
        assert files == []

    def test_path_property(self) -> None:
        files = parse_diff(NEW_FILE_DIFF)
        assert files[0].path == "src/new_module.py"
        files = parse_diff(DELETED_FILE_DIFF)
        assert files[0].path == "src/old_module.py"
        files = parse_diff(SIMPLE_MODIFY_DIFF)
        assert files[0].path == "src/app.py"


class TestGeneratedFileDetection:
    def test_lockfiles(self) -> None:
        assert is_generated("package-lock.json")
        assert is_generated("poetry.lock")
        assert is_generated("Cargo.lock")
        assert is_generated("go.sum")
        assert is_generated("yarn.lock")
        assert is_generated("Gemfile.lock")

    def test_nested_lockfile(self) -> None:
        assert is_generated("frontend/package-lock.json")

    def test_minified(self) -> None:
        assert is_generated("dist/app.min.js")
        assert is_generated("styles.min.css")

    def test_vendored(self) -> None:
        assert is_generated("vendor/lib/foo.go")
        assert is_generated("node_modules/pkg/index.js")
        assert is_generated("third_party/lib.c")

    def test_normal_files_not_generated(self) -> None:
        assert not is_generated("src/app.py")
        assert not is_generated("README.md")
        assert not is_generated("tests/test_app.py")

    def test_parse_diff_marks_generated(self) -> None:
        files = parse_diff(GENERATED_FILE_DIFF)
        assert len(files) == 1
        assert files[0].generated

    def test_vendored_diff_marked(self) -> None:
        files = parse_diff(VENDORED_DIFF)
        assert files[0].generated

    def test_minified_diff_marked(self) -> None:
        files = parse_diff(MINIFIED_DIFF)
        assert files[0].generated

    def test_custom_patterns(self) -> None:
        custom = ("custom.lock",)
        assert is_generated("custom.lock", custom)
        assert not is_generated("package-lock.json", custom)

    def test_proto_generated(self) -> None:
        assert is_generated("api/v1/service.pb.go")
        assert is_generated("internal/_generated.go")


class TestHunkHeaderEdgeCases:
    def test_single_line_hunk(self) -> None:
        diff = """\
diff --git a/f.py b/f.py
index abc..def 100644
--- a/f.py
+++ b/f.py
@@ -1 +1 @@
-old
+new
"""
        files = parse_diff(diff)
        hdr = files[0].hunks[0].header
        assert hdr.old_count == 1
        assert hdr.new_count == 1

    def test_no_newline_marker(self) -> None:
        diff = """\
diff --git a/f.py b/f.py
index abc..def 100644
--- a/f.py
+++ b/f.py
@@ -1,2 +1,2 @@
-old line
+new line
 context
\\ No newline at end of file
"""
        files = parse_diff(diff)
        assert files[0].additions == 1
        assert files[0].deletions == 1
