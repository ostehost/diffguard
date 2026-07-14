"""Contract and behavior tests for the composite GitHub Action."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import tomllib
from typing import Any

import pytest


ROOT = Path(__file__).parents[1]
NODE = shutil.which("node")


def _action_text() -> str:
    return (ROOT / "action.yml").read_text(encoding="utf-8")


def _review_script(cli_command: str | None = None) -> str:
    """Extract the review shell block, optionally substituting a test CLI."""
    action = _action_text()
    review_step = action.split("    - name: Run DiffGuard review\n", 1)[1]
    review_step = review_step.split("    - name: Post PR comment\n", 1)[0]
    script = textwrap.dedent(review_step.split("      run: |\n", 1)[1])
    if cli_command is not None:
        isolated = "\"$DIFFGUARD_PYTHON\" -I -c 'from diffguard.cli import main; main()'"
        assert isolated in script
        script = script.replace(isolated, cli_command)
    # Extracted shell steps do not receive action.yml's step-level env. Model
    # the runtime output explicitly while keeping paths with spaces quoted.
    return f"DIFFGUARD_PYTHON={shlex.quote(sys.executable)}\n{script}"


def _install_script() -> str:
    """Extract the isolated runtime installation shell block."""
    action = _action_text()
    install_step = action.split("    - name: Install DiffGuard\n", 1)[1]
    install_step = install_step.split("    - name: Run DiffGuard review\n", 1)[0]
    return textwrap.dedent(install_step.split("      run: |\n", 1)[1])


def _comment_script() -> str:
    """Extract the github-script block for behavior-level regression tests."""
    action = _action_text()
    comment_step = action.split("    - name: Post PR comment\n", 1)[1]
    comment_step = comment_step.split("    - name: Clean up DiffGuard runtime and findings\n", 1)[0]
    return textwrap.dedent(comment_step.split("        script: |\n", 1)[1])


def _cleanup_script() -> str:
    """Extract the temp-file cleanup shell block."""
    action = _action_text()
    cleanup_step = action.split("    - name: Clean up DiffGuard runtime and findings\n", 1)[1]
    return textwrap.dedent(cleanup_step.split("      run: |\n", 1)[1])


def _output_value(output_file: Path, name: str) -> str:
    output = output_file.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(name)}<<([^\n]+)\n(.*?)\n\1$", output, re.MULTILINE | re.DOTALL
    )
    assert match is not None
    return match.group(2)


def _single_line_output(output_file: Path, name: str) -> str:
    output = output_file.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}=(.*)$", output, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _comment_identity(base: str, head: str) -> str:
    """Return the exact prefix owned by one analyzed pull-request state."""
    return f"<!-- diffguard-review:v2 base={base} head={head} -->\n"


def _run_comment_script(
    tmp_path: Path,
    *,
    event_base: str,
    event_head: str,
    current_base: str,
    current_head: str,
    exit_code: str,
    analysis_incomplete: bool,
    findings: str,
    comments: list[dict[str, object]],
    comment_before_mutation: dict[str, object] | None = None,
    not_found_comment_ids: list[int] | None = None,
    current_after_mutation_base: str | None = None,
    current_after_mutation_head: str | None = None,
    created_comment_id: int = 99,
) -> dict[str, Any]:
    """Run the embedded JavaScript with deterministic, network-free API mocks."""
    assert NODE is not None
    comment_script = tmp_path / "comment-script.js"
    comment_script.write_text(_comment_script(), encoding="utf-8")
    findings_file = tmp_path / "findings.txt"
    findings_file.write_text(findings, encoding="utf-8")
    config = {
        "eventBase": event_base,
        "eventHead": event_head,
        "currentBase": current_base,
        "currentHead": current_head,
        "exitCode": exit_code,
        "analysisIncomplete": analysis_incomplete,
        "findingsFile": str(findings_file),
        "comments": comments,
        "commentBeforeMutation": comment_before_mutation,
        "notFoundCommentIds": not_found_comment_ids or [],
        "currentAfterMutationBase": current_after_mutation_base,
        "currentAfterMutationHead": current_after_mutation_head,
        "createdCommentId": created_comment_id,
    }
    harness = tmp_path / "comment-harness.js"
    harness.write_text(
        f"""
const fs = require('fs');
const AsyncFunction = Object.getPrototypeOf(async function() {{}}).constructor;
const script = fs.readFileSync({json.dumps(str(comment_script))}, 'utf8');
const config = {json.dumps(config)};
const calls = {{
  pullsGet: 0,
  graphql: 0,
  paginate: 0,
  update: [],
  create: [],
  delete: [],
  debug: [],
  notice: [],
  warning: [],
  commentAppearedBeforeMutation: false,
}};
function maybeRaiseNotFound(params) {{
  if (config.notFoundCommentIds.includes(params.comment_id)) {{
    const error = new Error(`comment ${{params.comment_id}} not found`);
    error.status = 404;
    throw error;
  }}
}}
const github = {{
  graphql: async () => {{
    calls.graphql += 1;
    return {{ viewer: {{ login: 'github-actions[bot]' }} }};
  }},
  paginate: async () => {{
    calls.paginate += 1;
    return config.comments;
  }},
  rest: {{
    pulls: {{
      get: async () => {{
        calls.pullsGet += 1;
        const usePostMutationState =
          calls.pullsGet > 1 && config.currentAfterMutationBase && config.currentAfterMutationHead;
        const response = {{
          data: {{
            base: {{
              sha: usePostMutationState ? config.currentAfterMutationBase : config.currentBase,
            }},
            head: {{
              sha: usePostMutationState ? config.currentAfterMutationHead : config.currentHead,
            }},
          }},
        }};
        if (config.commentBeforeMutation && !calls.commentAppearedBeforeMutation) {{
          config.comments.push(config.commentBeforeMutation);
          calls.commentAppearedBeforeMutation = true;
        }}
        return response;
      }},
    }},
    issues: {{
      listComments: async () => {{}},
      updateComment: async params => {{
        calls.update.push(params);
        maybeRaiseNotFound(params);
      }},
      createComment: async params => {{
        calls.create.push(params);
        return {{ data: {{ id: config.createdCommentId }} }};
      }},
      deleteComment: async params => {{
        calls.delete.push(params);
        maybeRaiseNotFound(params);
      }},
    }},
  }},
}};
const context = {{
  repo: {{ owner: 'owner', repo: 'repo' }},
  issue: {{ number: 17 }},
  payload: {{
    pull_request: {{
      base: {{ sha: config.eventBase }},
      head: {{ sha: config.eventHead }},
    }},
  }},
}};
const core = {{
  debug: message => calls.debug.push(message),
  notice: message => calls.notice.push(message),
  warning: message => calls.warning.push(message),
}};
process.env.DIFFGUARD_EXIT_CODE = config.exitCode;
process.env.DIFFGUARD_ANALYSIS_INCOMPLETE = String(config.analysisIncomplete);
process.env.DIFFGUARD_COMMENT_FILE = config.findingsFile;
const run = new AsyncFunction('github', 'context', 'core', 'require', script);
run(github, context, core, require)
  .then(() => console.log(JSON.stringify(calls)))
  .catch(error => {{
    console.error(error);
    process.exit(1);
  }});
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [NODE, str(harness)],
        check=True,
        capture_output=True,
        text=True,
    )
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_action_installs_selected_checkout_not_pypi() -> None:
    action = _action_text()
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0" in action
    assert "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0" in action
    assert "actions/setup-python@v" not in action
    assert "actions/github-script@v" not in action
    assert "DIFFGUARD_ACTION_PATH: ${{ github.action_path }}" in action
    assert (
        "DIFFGUARD_RUNTIME_CONSTRAINTS: "
        "${{ github.action_path }}/action-runtime-constraints.txt" in action
    )
    assert (
        "DIFFGUARD_BUILD_CONSTRAINTS: "
        "${{ github.action_path }}/action-build-constraints.txt" in action
    )
    assert '"$RUNTIME_PYTHON" -I -m pip install' in action
    assert "python -I -m pip install" not in action
    assert '--constraint "$DIFFGUARD_BUILD_CONSTRAINTS"' in action
    assert '"hatchling==1.31.0"' in action
    assert "--no-build-isolation" in action
    assert '--constraint "$DIFFGUARD_RUNTIME_CONSTRAINTS"' in action
    assert '"$DIFFGUARD_ACTION_PATH"' in action
    assert "pip install diffguard" not in action
    assert "findings<<%s" in action
    assert "DIFFGUARD_EOF" not in action
    assert "[ $EXIT_CODE -ne 0 ] && [ $EXIT_CODE -ne 1 ]" in action
    assert "DIFFGUARD_REF_RANGE: ${{ inputs.ref-range }}" in action
    assert "DIFFGUARD_FORMAT: ${{ inputs.format }}" in action
    assert "DIFFGUARD_POST_COMMENT: ${{ inputs.post-comment }}" in action
    assert "DIFFGUARD_EVENT_NAME: ${{ github.event_name }}" in action
    assert "DIFFGUARD_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in action
    assert "DIFFGUARD_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in action
    assert 'REF_RANGE="${{ inputs.ref-range }}"' not in action
    assert '--format "${{ inputs.format }}"' not in action
    assert 'REF_RANGE="${DIFFGUARD_BASE_SHA}...${DIFFGUARD_HEAD_SHA}"' in action
    assert "2>&1" not in action
    assert '2>"$STDERR_FILE"' in action
    assert 'cat "$STDERR_FILE" >&2' not in action
    assert "OUTPUT_LIMIT_BYTES=250000" in action
    assert "findings-truncated" in action
    assert "analysis-incomplete" in action
    assert "findings-file" in action
    assert "comment-file" in action
    assert "DIFFGUARD_FINDINGS_FILE" in action
    assert "DIFFGUARD_COMMENT_FILE" in action
    assert "DIFFGUARD_FINDINGS:" not in action


@pytest.mark.parametrize(
    ("runner_os", "native_runtime", "interpreter_suffix", "expects_cygpath"),
    [
        ("Linux", None, "bin/python", False),
        ("Windows", r"C:\runner temp\diffguard-runtime-abc", "Scripts/python.exe", True),
    ],
)
def test_action_installs_into_portable_per_run_venv(
    tmp_path: Path,
    runner_os: str,
    native_runtime: str | None,
    interpreter_suffix: str,
    expects_cygpath: bool,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime_dir = tmp_path / "runtime with spaces"
    native_runtime = native_runtime or str(runtime_dir)
    venv_target_file = tmp_path / "venv-target"
    install_args_file = tmp_path / "install-args"
    cygpath_input_file = tmp_path / "cygpath-input"
    runtime_stub = tmp_path / "runtime-python"
    runtime_stub.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "printf '%s\\n' CALL >> \"$INSTALL_ARGS_FILE\"\n"
        'printf \'%s\\n\' "$@" >> "$INSTALL_ARGS_FILE"\n',
        encoding="utf-8",
    )
    runtime_stub.chmod(0o755)

    setup_python = bin_dir / "python"
    setup_python.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [ "${2:-}" = "-c" ]; then\n'
        "  printf '%s\\n' \"$NATIVE_RUNTIME_DIR\"\n"
        "  exit 0\n"
        "fi\n"
        'test "${2:-}" = "-m"\n'
        'test "${3:-}" = "venv"\n'
        "target=$4\n"
        'printf \'%s\\n\' "$target" > "$VENV_TARGET_FILE"\n'
        'destination="$target/$INTERPRETER_SUFFIX"\n'
        'mkdir -p "$(dirname "$destination")"\n'
        'cp "$RUNTIME_STUB" "$destination"\n'
        'chmod +x "$destination"\n',
        encoding="utf-8",
    )
    setup_python.chmod(0o755)

    cygpath = bin_dir / "cygpath"
    cygpath.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'test "$1" = "-u"\n'
        'printf \'%s\\n\' "$2" > "$CYGPATH_INPUT_FILE"\n'
        "printf '%s\\n' \"$CONVERTED_RUNTIME_DIR\"\n",
        encoding="utf-8",
    )
    cygpath.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _install_script()],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_OS": runner_os,
            "RUNNER_TEMP": str(tmp_path),
            "NATIVE_RUNTIME_DIR": native_runtime,
            "CONVERTED_RUNTIME_DIR": str(runtime_dir),
            "INTERPRETER_SUFFIX": interpreter_suffix,
            "RUNTIME_STUB": str(runtime_stub),
            "VENV_TARGET_FILE": str(venv_target_file),
            "INSTALL_ARGS_FILE": str(install_args_file),
            "CYGPATH_INPUT_FILE": str(cygpath_input_file),
            "DIFFGUARD_ACTION_PATH": str(ROOT),
            "DIFFGUARD_BUILD_CONSTRAINTS": str(ROOT / "action-build-constraints.txt"),
            "DIFFGUARD_RUNTIME_CONSTRAINTS": str(ROOT / "action-runtime-constraints.txt"),
            "GITHUB_OUTPUT": str(output_file),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    expected_python = runtime_dir / interpreter_suffix
    assert venv_target_file.read_text(encoding="utf-8").strip() == str(runtime_dir)
    assert _single_line_output(output_file, "runtime-dir") == str(runtime_dir)
    assert _single_line_output(output_file, "python-path") == str(expected_python)
    assert install_args_file.read_text(encoding="utf-8").splitlines() == [
        "CALL",
        "-I",
        "-m",
        "pip",
        "install",
        "--constraint",
        str(ROOT / "action-build-constraints.txt"),
        "hatchling==1.31.0",
        "CALL",
        "-I",
        "-m",
        "pip",
        "install",
        "--no-build-isolation",
        "--constraint",
        str(ROOT / "action-runtime-constraints.txt"),
        str(ROOT),
    ]
    assert cygpath_input_file.exists() is expects_cygpath
    if expects_cygpath:
        assert cygpath_input_file.read_text(encoding="utf-8").strip() == native_runtime


def test_action_python_helpers_use_isolated_mode() -> None:
    action = _action_text()

    assert action.count('"$DIFFGUARD_PYTHON" -I -c') == 7
    assert '"$RUNTIME_PYTHON" -I -m pip install' in action
    assert "python -I -m pip install" not in action
    assert (
        'OUTPUT=$("$DIFFGUARD_PYTHON" -I -c '
        "'from diffguard.cli import main; main()' review" in action
    )
    assert "OUTPUT=$(diffguard review" not in action
    assert "python -c" not in action
    assert "python -m pip" not in action


def test_action_runtime_constraints_match_locked_runtime_closure() -> None:
    expected_packages = {
        "annotated-types",
        "click",
        "colorama",
        "markdown-it-py",
        "mdurl",
        "pydantic",
        "pydantic-core",
        "pygments",
        "rich",
        "tree-sitter",
        "tree-sitter-go",
        "tree-sitter-javascript",
        "tree-sitter-python",
        "tree-sitter-typescript",
        "typing-extensions",
        "typing-inspection",
    }
    constraints_text = (ROOT / "action-runtime-constraints.txt").read_text(encoding="utf-8")
    constraints: dict[str, str] = {}
    for raw_line in constraints_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        requirement = line.split(";", 1)[0].strip()
        name, separator, version = requirement.partition("==")
        assert separator == "==", raw_line
        assert name not in constraints, name
        constraints[name] = version

    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_versions = {package["name"]: package["version"] for package in lock["package"]}
    assert constraints.keys() == expected_packages
    assert constraints == {name: locked_versions[name] for name in expected_packages}
    assert "colorama==0.4.6 ; sys_platform == 'win32'" in constraints_text

    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "exact runtime dependency versions exported from `uv.lock`" in readme
    assert "complete, exact PEP 517 backend closure" in readme


def test_action_build_constraints_pin_the_complete_backend_closure() -> None:
    constraints_text = (ROOT / "action-build-constraints.txt").read_text(encoding="utf-8")
    constraints: dict[str, str] = {}
    for raw_line in constraints_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("==")
        assert separator == "==", raw_line
        assert name not in constraints, name
        constraints[name] = version

    assert constraints == {
        "hatchling": "1.31.0",
        "packaging": "26.2",
        "pathspec": "1.1.1",
        "pluggy": "1.6.0",
        "trove-classifiers": "2026.6.1.19",
    }


def test_action_isolated_cli_ignores_hostile_pythonpath(tmp_path: Path) -> None:
    shadow_root = tmp_path / "shadow"
    shadow_package = shadow_root / "diffguard"
    shadow_package.mkdir(parents=True)
    (shadow_package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "shadow-imported"
    (shadow_package / "cli.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['SHADOW_MARKER']).write_text('imported', encoding='utf-8')\n"
        "raise RuntimeError('hostile PYTHONPATH shadow imported')\n",
        encoding="utf-8",
    )
    output_file = tmp_path / "github-output"

    result = subprocess.run(
        ["bash", "-c", _review_script()],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(shadow_root),
            "SHADOW_MARKER": str(marker),
            "DIFFGUARD_REF_RANGE": "HEAD..HEAD",
            "DIFFGUARD_FORMAT": "text",
            "DIFFGUARD_POST_COMMENT": "false",
            "DIFFGUARD_EVENT_NAME": "push",
            "DIFFGUARD_BASE_SHA": "",
            "DIFFGUARD_HEAD_SHA": "",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    assert _single_line_output(output_file, "exit-code") == "0"
    Path(_single_line_output(output_file, "findings-file")).unlink()
    Path(_single_line_output(output_file, "comment-file")).unlink()


def test_setup_python_v6_declares_self_hosted_runner_floor() -> None:
    action = _action_text()

    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0" in action
    assert "# setup-python v6 uses Node 24. Self-hosted consumers require GitHub Actions" in action
    assert "# Runner v2.327.1+; GitHub-hosted runners are managed by GitHub." in action


def test_action_rejects_missing_pr_context(tmp_path: Path) -> None:
    output_file = tmp_path / "github-output"
    env = {
        **os.environ,
        "DIFFGUARD_REF_RANGE": "",
        "DIFFGUARD_FORMAT": "text",
        "DIFFGUARD_BASE_SHA": "",
        "DIFFGUARD_HEAD_SHA": "",
        "GITHUB_OUTPUT": str(output_file),
    }

    result = subprocess.run(
        ["bash", "-c", _review_script()],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "ref-range is required outside a pull_request event" in result.stdout
    assert output_file.read_text(encoding="utf-8") == "exit-code=2\n"


def test_action_rejects_custom_range_for_comment_enabled_pr(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invoked_file = tmp_path / "invoked"
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['INVOKED_FILE']).write_text('invoked', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script()],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "INVOKED_FILE": str(invoked_file),
            "DIFFGUARD_REF_RANGE": "custom-base..custom-head",
            "DIFFGUARD_FORMAT": "text",
            "DIFFGUARD_POST_COMMENT": "true",
            "DIFFGUARD_EVENT_NAME": "pull_request",
            "DIFFGUARD_BASE_SHA": "event-base",
            "DIFFGUARD_HEAD_SHA": "event-head",
            "GITHUB_OUTPUT": str(output_file),
        },
    )

    assert result.returncode == 2
    assert "custom ref-range is not allowed when post-comment=true" in result.stdout
    assert "comment identity matches the reviewed base/head" in result.stdout
    assert output_file.read_text(encoding="utf-8") == "exit-code=2\n"
    assert not invoked_file.exists()


@pytest.mark.parametrize(
    ("post_comment", "event_name"),
    [("false", "pull_request"), ("true", "push")],
)
def test_action_allows_custom_range_without_pr_commenting(
    tmp_path: Path, post_comment: str, event_name: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_file = tmp_path / "args.json"
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import sys\n"
        "Path(os.environ['ARGS_FILE']).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "ARGS_FILE": str(args_file),
            "DIFFGUARD_REF_RANGE": "custom-base..custom-head",
            "DIFFGUARD_FORMAT": "text",
            "DIFFGUARD_POST_COMMENT": post_comment,
            "DIFFGUARD_EVENT_NAME": event_name,
            "DIFFGUARD_BASE_SHA": "event-base",
            "DIFFGUARD_HEAD_SHA": "event-head",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 0
    assert "custom ref-range is not allowed" not in result.stdout
    assert json.loads(args_file.read_text(encoding="utf-8")) == [
        "review",
        "custom-base..custom-head",
        "--format",
        "text",
    ]
    assert _single_line_output(output_file, "exit-code") == "0"
    Path(_single_line_output(output_file, "findings-file")).unlink()
    Path(_single_line_output(output_file, "comment-file")).unlink()


@pytest.mark.parametrize("ref_range", ["--help", "--staged"])
def test_action_rejects_option_like_ref_range(tmp_path: Path, ref_range: str) -> None:
    output_file = tmp_path / "github-output"
    env = {
        **os.environ,
        "DIFFGUARD_REF_RANGE": ref_range,
        "DIFFGUARD_FORMAT": "text",
        "DIFFGUARD_BASE_SHA": "",
        "DIFFGUARD_HEAD_SHA": "",
        "GITHUB_OUTPUT": str(output_file),
        "RUNNER_TEMP": str(tmp_path),
    }

    result = subprocess.run(
        ["bash", "-c", _review_script()],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 2
    assert "ref-range must not begin with '-'" in result.stdout
    assert "Usage: diffguard review" not in result.stdout
    assert output_file.read_text(encoding="utf-8") == "exit-code=2\n"


def test_action_windows_uses_shell_temp_paths_and_emits_node_paths(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shell_temp_root = tmp_path / "converted temp"
    shell_temp_root.mkdir()
    native_temp_root = r"C:\runner temp"
    mixed_temp_root = "C:/runner temp"
    cygpath_calls = tmp_path / "cygpath-calls"
    mktemp_templates = tmp_path / "mktemp-templates"

    cygpath = bin_dir / "cygpath"
    cygpath.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf \'%s\\t%s\\n\' "$1" "$2" >> "$CYGPATH_CALLS"\n'
        'if [ "$1" = "-u" ]; then\n'
        '  case "$2" in\n'
        '    "$NATIVE_TEMP_ROOT") printf \'%s\\n\' "$SHELL_TEMP_ROOT" ;;\n'
        '    "$MIXED_TEMP_ROOT"/*) printf \'%s/%s\\n\' "$SHELL_TEMP_ROOT" "${2##*/}" ;;\n'
        "    *) printf '%s\\n' \"$2\" ;;\n"
        "  esac\n"
        'elif [ "$1" = "-m" ]; then\n'
        '  printf \'%s/%s\\n\' "$MIXED_TEMP_ROOT" "${2##*/}"\n'
        "else\n"
        "  exit 2\n"
        "fi\n",
        encoding="utf-8",
    )
    cygpath.chmod(0o755)

    fake_mktemp = bin_dir / "mktemp"
    fake_mktemp.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "template=$1\n"
        'printf \'%s\\n\' "$template" >> "$MKTEMP_TEMPLATES"\n'
        'case "$template" in\n'
        "  *diffguard-stderr.*) suffix=stderr ;;\n"
        "  *diffguard-findings.*) suffix=findings ;;\n"
        "  *diffguard-comment.*) suffix=comment ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
        'path="${template%XXXXXX}$suffix"\n'
        ': > "$path"\n'
        "printf '%s\\n' \"$path\"\n",
        encoding="utf-8",
    )
    fake_mktemp.chmod(0o755)

    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    common_env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_OS": "Windows",
        "RUNNER_TEMP": native_temp_root,
        "NATIVE_TEMP_ROOT": native_temp_root,
        "MIXED_TEMP_ROOT": mixed_temp_root,
        "SHELL_TEMP_ROOT": str(shell_temp_root),
        "CYGPATH_CALLS": str(cygpath_calls),
        "MKTEMP_TEMPLATES": str(mktemp_templates),
        "DIFFGUARD_REF_RANGE": "base...head",
        "DIFFGUARD_FORMAT": "text",
        "DIFFGUARD_POST_COMMENT": "false",
        "DIFFGUARD_EVENT_NAME": "push",
        "DIFFGUARD_BASE_SHA": "",
        "DIFFGUARD_HEAD_SHA": "",
        "GITHUB_OUTPUT": str(output_file),
    }
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env=common_env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert mktemp_templates.read_text(encoding="utf-8").splitlines() == [
        str(shell_temp_root / "diffguard-stderr.XXXXXX"),
        str(shell_temp_root / "diffguard-findings.XXXXXX"),
        str(shell_temp_root / "diffguard-comment.XXXXXX"),
    ]
    findings_output = _single_line_output(output_file, "findings-file")
    comment_output = _single_line_output(output_file, "comment-file")
    assert findings_output == f"{mixed_temp_root}/diffguard-findings.findings"
    assert comment_output == f"{mixed_temp_root}/diffguard-comment.comment"
    findings_shell = shell_temp_root / "diffguard-findings.findings"
    comment_shell = shell_temp_root / "diffguard-comment.comment"
    assert findings_shell.exists()
    assert comment_shell.exists()

    runtime_dir = shell_temp_root / "diffguard-runtime-cleanup"
    runtime_dir.mkdir()
    cleanup = subprocess.run(
        ["bash", "-c", _cleanup_script()],
        check=False,
        capture_output=True,
        text=True,
        env={
            **common_env,
            "DIFFGUARD_RUNTIME_DIR": str(runtime_dir),
            "DIFFGUARD_FINDINGS_FILE": findings_output,
            "DIFFGUARD_COMMENT_FILE": comment_output,
        },
    )

    assert cleanup.returncode == 0, cleanup.stdout + cleanup.stderr
    assert not findings_shell.exists()
    assert not comment_shell.exists()
    assert not runtime_dir.exists()
    assert cygpath_calls.read_text(encoding="utf-8").splitlines() == [
        f"-u\t{native_temp_root}",
        f"-m\t{findings_shell}",
        f"-m\t{comment_shell}",
        f"-u\t{findings_output}",
        f"-u\t{comment_output}",
    ]


def test_action_bounds_declared_output_but_logs_full_output(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write('x' * 500_000)\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DIFFGUARD_REF_RANGE": "base...head",
        "DIFFGUARD_FORMAT": "text",
        "DIFFGUARD_BASE_SHA": "",
        "DIFFGUARD_HEAD_SHA": "",
        "GITHUB_OUTPUT": str(output_file),
        "RUNNER_TEMP": str(tmp_path),
    }

    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.count("x") == 500_000
    output = output_file.read_text(encoding="utf-8")
    assert "findings-truncated=true\n" in output
    findings = _output_value(output_file, "findings")
    assert len(findings.encode("utf-8")) <= 250_000
    assert findings.endswith(
        "[DiffGuard findings output truncated; full output is in the step log.]"
    )
    findings_file = Path(_single_line_output(output_file, "findings-file"))
    assert findings_file.parent == tmp_path
    assert findings_file.read_text(encoding="utf-8") == "x" * 500_000
    comment_file = Path(_single_line_output(output_file, "comment-file"))
    assert comment_file.read_text(encoding="utf-8") == "x" * 500_000
    findings_file.unlink()
    comment_file.unlink()


def test_action_serializes_source_output_inside_stop_commands(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write('::warning::stdout injection\\n')\n"
        "sys.stderr.write('::error::stderr injection\\n')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "DIFFGUARD_REF_RANGE": "base...head",
        "DIFFGUARD_FORMAT": "text",
        "DIFFGUARD_BASE_SHA": "",
        "DIFFGUARD_HEAD_SHA": "",
        "GITHUB_OUTPUT": str(output_file),
        "RUNNER_TEMP": str(tmp_path),
    }

    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    stop_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("::stop-commands::DIFFGUARD_LOG_")
    )
    token = lines[stop_index].removeprefix("::stop-commands::")
    resume_index = lines.index(f"::{token}::")
    assert stop_index < lines.index("::error::stderr injection") < resume_index
    assert stop_index < lines.index("::warning::stdout injection") < resume_index
    Path(_single_line_output(output_file, "findings-file")).unlink()
    Path(_single_line_output(output_file, "comment-file")).unlink()


def test_action_resume_command_has_a_boundary_after_unterminated_stderr(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('unterminated diagnostic')\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DIFFGUARD_REF_RANGE": "base...head",
            "DIFFGUARD_FORMAT": "text",
            "DIFFGUARD_BASE_SHA": "",
            "DIFFGUARD_HEAD_SHA": "",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 2
    lines = result.stdout.splitlines()
    stop_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("::stop-commands::DIFFGUARD_LOG_")
    )
    token = lines[stop_index].removeprefix("::stop-commands::")
    assert "unterminated diagnostic" in lines
    resume_index = lines.index(f"::{token}::")
    error_index = lines.index("::error::DiffGuard encountered an error (exit 2)")
    assert stop_index < lines.index("unterminated diagnostic") < resume_index < error_index
    Path(_single_line_output(output_file, "findings-file")).unlink()
    Path(_single_line_output(output_file, "comment-file")).unlink()


def test_action_json_log_escapes_controls_without_changing_parsed_values(
    tmp_path: Path,
) -> None:
    unsafe = "c1:\x85 bidi:\u202e isolate:\u2066"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        "#!/usr/bin/env python3\n"
        "from diffguard.engine.findings import Finding\n"
        "from diffguard.report import render_json\n"
        "from diffguard.schema import DiffStats, DiffGuardOutput, FileChange, Meta, SymbolChange\n"
        f"unsafe = {unsafe!r}\n"
        "change = SymbolChange(\n"
        "    kind='signature_changed',\n"
        "    name=unsafe,\n"
        "    before_signature='def old()',\n"
        "    after_signature='def new()',\n"
        "    breaking=True,\n"
        "    confidence='high',\n"
        "    rule_id='DG110',\n"
        "    category_id='signature_changed',\n"
        "    category='CHANGED',\n"
        "    evidence=['signature changed'],\n"
        ")\n"
        "file = FileChange(\n"
        "    path='src/mod.py',\n"
        "    language='python',\n"
        "    change_type='modified',\n"
        "    changes=[change],\n"
        ")\n"
        "output = DiffGuardOutput(\n"
        "    meta=Meta(\n"
        "        ref_range='base...head',\n"
        "        stats=DiffStats(files=1, additions=0, deletions=0),\n"
        "    ),\n"
        "    files=[file],\n"
        ")\n"
        "finding = Finding(file=file, change=change, category='CHANGED')\n"
        "print(render_json(output, 'base...head', 'committed', [finding]))\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DIFFGUARD_REF_RANGE": "base...head",
            "DIFFGUARD_FORMAT": "json",
            "DIFFGUARD_POST_COMMENT": "false",
            "DIFFGUARD_EVENT_NAME": "push",
            "DIFFGUARD_BASE_SHA": "",
            "DIFFGUARD_HEAD_SHA": "",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    for control in ("\x85", "\u202e", "\u2066"):
        assert control not in result.stdout
    assert r"\u0085" in result.stdout
    assert r"\u202e" in result.stdout
    assert r"\u2066" in result.stdout

    findings = json.loads(_output_value(output_file, "findings"))
    assert findings["findings"][0]["symbol"] == unsafe
    findings_file = Path(_single_line_output(output_file, "findings-file"))
    comment_file = Path(_single_line_output(output_file, "comment-file"))
    assert json.loads(findings_file.read_text(encoding="utf-8"))["findings"][0]["symbol"] == unsafe
    assert comment_file.read_text(encoding="utf-8") == findings_file.read_text(encoding="utf-8")
    findings_file.unlink()
    comment_file.unlink()


@pytest.mark.parametrize(
    ("payload", "expected_incomplete"),
    [
        (
            {
                "version": "1.1.0",
                "status": "ok",
                "mode": "committed",
                "ref_range": "base...head",
                "findings": [],
                "warnings": [],
                "stats": {
                    "files_analyzed": 1,
                    "symbols_changed": 0,
                    "parse_errors": 0,
                    "reference_count": 0,
                    "silence_reason": "no high-signal changes",
                },
                "error": None,
            },
            "false",
        ),
        (
            {
                "version": "1.1.0",
                "status": "ok",
                "mode": "committed",
                "ref_range": "base...head",
                "findings": [],
                "warnings": [
                    {
                        "code": "parse_gap",
                        "message": "broken.py: parse gap",
                        "file": "broken.py",
                    }
                ],
                "stats": {
                    "files_analyzed": 1,
                    "symbols_changed": 0,
                    "parse_errors": 1,
                    "reference_count": 0,
                    "silence_reason": "no high-signal changes",
                },
                "error": None,
            },
            "true",
        ),
        (
            {
                "warnings": [],
                "stats": {"parse_errors": 0},
            },
            "true",
        ),
    ],
)
def test_action_classifies_structured_analysis_gaps(
    tmp_path: Path, payload: dict[str, object], expected_incomplete: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    fake_diffguard.write_text(
        f"#!/usr/bin/env python3\nimport json\nprint(json.dumps({payload!r}))\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DIFFGUARD_REF_RANGE": "base...head",
            "DIFFGUARD_FORMAT": "json",
            "DIFFGUARD_BASE_SHA": "",
            "DIFFGUARD_HEAD_SHA": "",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 0
    assert _single_line_output(output_file, "analysis-incomplete") == expected_incomplete
    if expected_incomplete == "true":
        assert "::warning::DiffGuard analysis is incomplete" in result.stdout
        if "broken.py" in str(payload):
            assert "broken.py" in result.stdout
    else:
        assert "::warning::DiffGuard analysis is incomplete" not in result.stdout
    findings_file = Path(_single_line_output(output_file, "findings-file"))
    comment_file = Path(_single_line_output(output_file, "comment-file"))
    assert json.loads(findings_file.read_text(encoding="utf-8")) == payload
    assert comment_file.read_text(encoding="utf-8") == findings_file.read_text(encoding="utf-8")
    findings_file.unlink()
    comment_file.unlink()


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_action_captures_text_warnings_for_incomplete_comment(
    tmp_path: Path, line_ending: str
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_diffguard = bin_dir / "diffguard"
    warning_output = (
        f"DiffGuard analysis warnings:{line_ending}- broken.py: parse gap{line_ending}"
    ).encode()
    fake_diffguard.write_text(
        f"#!/usr/bin/env python3\nimport sys\nsys.stderr.buffer.write({warning_output!r})\n",
        encoding="utf-8",
    )
    fake_diffguard.chmod(0o755)

    output_file = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-c", _review_script("diffguard")],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "DIFFGUARD_REF_RANGE": "base...head",
            "DIFFGUARD_FORMAT": "text",
            "DIFFGUARD_BASE_SHA": "",
            "DIFFGUARD_HEAD_SHA": "",
            "GITHUB_OUTPUT": str(output_file),
            "RUNNER_TEMP": str(tmp_path),
        },
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert _single_line_output(output_file, "analysis-incomplete") == "true"
    comment_file = Path(_single_line_output(output_file, "comment-file"))
    assert comment_file.read_bytes() == warning_output
    Path(_single_line_output(output_file, "findings-file")).unlink()
    comment_file.unlink()


def test_action_cleanup_removes_findings_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime with spaces"
    runtime_dir.mkdir()
    (runtime_dir / "installed-package").write_text("installed", encoding="utf-8")
    findings_file = tmp_path / "findings with spaces.txt"
    findings_file.write_text("findings", encoding="utf-8")
    comment_file = tmp_path / "comment with spaces.txt"
    comment_file.write_text("comment", encoding="utf-8")

    result = subprocess.run(
        ["bash", "-c", _cleanup_script()],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DIFFGUARD_RUNTIME_DIR": str(runtime_dir),
            "DIFFGUARD_FINDINGS_FILE": str(findings_file),
            "DIFFGUARD_COMMENT_FILE": str(comment_file),
        },
    )

    assert result.returncode == 0
    assert not runtime_dir.exists()
    assert not findings_file.exists()
    assert not comment_file.exists()


def test_action_safely_renders_bounded_pr_comment() -> None:
    action = _action_text()
    assert "escapeAndTruncate" in action
    assert "unsafeDisplayEscape" in action
    assert "maxCommentBytes = 60_000" in action
    assert "'&': '&amp;'" in action
    assert "'<': '&lt;'" in action
    assert "'>': '&gt;'" in action
    assert "DiffGuard output truncated" in action
    assert "<pre>" in action


def test_action_reconciles_only_action_owned_paginated_comments() -> None:
    action = _action_text()
    assert "<!-- diffguard-review -->" not in _comment_identity("base-1", "head-1")
    assert 'default: "false"' in action
    assert "steps.review.outputs.exit-code == '0'" in action
    assert "steps.review.outputs.exit-code == '1'" in action
    assert "DIFFGUARD_EXIT_CODE" in action
    assert "DIFFGUARD_ANALYSIS_INCOMPLETE" in action
    assert "github.paginate(github.rest.issues.listComments" in action
    assert "github-actions[bot]" in action
    assert "commentAuthors.has(comment.user?.login)" in action
    assert "diffguard-review:v2 base=${analyzedBase} head=${analyzedHead}" in action
    assert "comment.body?.startsWith(commentIdentity)" in action
    assert "if (exitCode === '0' && !analysisIncomplete)" in action
    assert "github.rest.issues.deleteComment" in action
    assert "github.rest.pulls.get" in action
    assert "currentPullRequest.base.sha" in action
    assert "currentPullRequest.head.sha" in action


def test_comment_enabled_example_serializes_each_pr_job() -> None:
    workflow = (ROOT / "examples" / "diffguard-workflow.yml").read_text(encoding="utf-8")

    assert (
        "  diffguard:\n"
        "    concurrency:\n"
        "      group: ${{ github.workflow }}-diffguard-"
        "${{ github.event.pull_request.number || github.run_id }}\n"
        "      cancel-in-progress: false\n"
    ) in workflow


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
@pytest.mark.parametrize(
    ("current_base", "current_head"),
    [("base-2", "head-1"), ("base-1", "head-2")],
)
def test_action_stale_pr_payload_performs_no_comment_mutation(
    tmp_path: Path, current_base: str, current_head: str
) -> None:
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base=current_base,
        current_head=current_head,
        exit_code="0",
        analysis_incomplete=False,
        findings="",
        comments=[
            {
                "id": 1,
                "body": _comment_identity("base-1", "head-1"),
                "user": {"login": "github-actions[bot]"},
            }
        ],
    )

    assert calls["pullsGet"] == 1
    assert calls["graphql"] == 1
    assert calls["paginate"] == 1
    assert calls["update"] == []
    assert calls["create"] == []
    assert calls["delete"] == []
    assert calls["notice"] == [
        "DiffGuard comment skipped because the pull request advanced during review."
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_new_pr_state_creates_distinct_comment_identity(tmp_path: Path) -> None:
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-2",
        current_base="base-1",
        current_head="head-2",
        exit_code="1",
        analysis_incomplete=False,
        findings="new-state finding",
        comments=[
            {
                "id": 1,
                "body": _comment_identity("base-1", "head-1") + "older-state finding",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": "<!-- diffguard-review -->\nlegacy unversioned finding",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 3,
                "body": "<!-- diffguard-review:v3 base=base-1 head=head-2 -->\nfuture version",
                "user": {"login": "github-actions[bot]"},
            },
        ],
    )

    assert calls["pullsGet"] == 2
    assert calls["update"] == []
    assert calls["delete"] == []
    assert len(calls["create"]) == 1
    body = calls["create"][0]["body"]
    assert body.startswith(_comment_identity("base-1", "head-2"))
    assert "<sub>Analyzed <code>base-1...head-2</code>" in body


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_race_never_mutates_newer_state_comments(tmp_path: Path) -> None:
    old_identity = _comment_identity("base-1", "head-1")
    new_identity = _comment_identity("base-1", "head-2")
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="1",
        analysis_incomplete=False,
        findings="old-state rerun",
        comments=[
            {
                "id": 1,
                "body": old_identity + "old-state primary",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": old_identity + "old-state duplicate",
                "user": {"login": "github-actions[bot]"},
            },
        ],
        # The PR-state check above returns the H1 snapshot, then this H2 comment
        # appears before the H1 mutation calls execute.
        comment_before_mutation={
            "id": 3,
            "body": new_identity + old_identity + "newer state created during the race",
            "user": {"login": "github-actions[bot]"},
        },
    )

    assert calls["commentAppearedBeforeMutation"] is True
    assert calls["pullsGet"] == 2
    assert [call["comment_id"] for call in calls["update"]] == [1]
    assert [call["comment_id"] for call in calls["delete"]] == [2]
    assert calls["create"] == []
    assert calls["update"][0]["body"].startswith(old_identity)


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_clean_snapshot_never_deletes_later_comment(tmp_path: Path) -> None:
    current_identity = _comment_identity("base-1", "head-1")
    newer_identity = _comment_identity("base-1", "head-2")
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="0",
        analysis_incomplete=False,
        findings="",
        comments=[
            {
                "id": 1,
                "body": current_identity + "current-state finding",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": "<!-- diffguard-review -->\nlegacy finding",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 4,
                "body": "<!-- diffguard-review:v3 base=base-1 head=head-1 -->\nfuture version",
                "user": {"login": "github-actions[bot]"},
            },
        ],
        comment_before_mutation={
            "id": 3,
            "body": newer_identity + "newer state created after the snapshot",
            "user": {"login": "github-actions[bot]"},
        },
    )

    assert calls["commentAppearedBeforeMutation"] is True
    assert calls["pullsGet"] == 1
    assert calls["update"] == []
    assert calls["create"] == []
    assert [call["comment_id"] for call in calls["delete"]] == [1]


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_new_clean_state_leaves_prior_findings_scoped_to_old_head(tmp_path: Path) -> None:
    finding_calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="1",
        analysis_incomplete=False,
        findings="old-state finding",
        comments=[],
        created_comment_id=41,
    )
    assert len(finding_calls["create"]) == 1
    old_body = finding_calls["create"][0]["body"]
    assert old_body.startswith(_comment_identity("base-1", "head-1"))
    assert "<sub>Analyzed <code>base-1...head-1</code>" in old_body

    clean_calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-2",
        current_base="base-1",
        current_head="head-2",
        exit_code="0",
        analysis_incomplete=False,
        findings="",
        comments=[
            {
                "id": 41,
                "body": old_body,
                "user": {"login": "github-actions[bot]"},
            }
        ],
    )

    assert clean_calls["pullsGet"] == 1
    assert clean_calls["update"] == []
    assert clean_calls["create"] == []
    assert clean_calls["delete"] == []


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_post_write_staleness_rolls_back_only_own_state(tmp_path: Path) -> None:
    own_identity = _comment_identity("base-1", "head-1")
    prior_identity = _comment_identity("base-0", "head-0")
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        current_after_mutation_base="base-1",
        current_after_mutation_head="head-2",
        exit_code="1",
        analysis_incomplete=False,
        findings="finding that became stale",
        comments=[
            {
                "id": 1,
                "body": own_identity + "primary",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": own_identity + "duplicate",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 3,
                "body": prior_identity + "prior state",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 4,
                "body": "<!-- diffguard-review -->\nlegacy state",
                "user": {"login": "github-actions[bot]"},
            },
        ],
    )

    assert calls["pullsGet"] == 2
    assert [call["comment_id"] for call in calls["update"]] == [1]
    assert [call["comment_id"] for call in calls["delete"]] == [1, 2]
    assert calls["create"] == []
    assert calls["notice"] == [
        "DiffGuard comment skipped because the pull request advanced during review."
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_post_create_staleness_deletes_created_comment(tmp_path: Path) -> None:
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        current_after_mutation_base="base-1",
        current_after_mutation_head="head-2",
        exit_code="1",
        analysis_incomplete=False,
        findings="new comment that became stale",
        comments=[],
        created_comment_id=77,
    )

    assert calls["pullsGet"] == 2
    assert calls["update"] == []
    assert len(calls["create"]) == 1
    assert [call["comment_id"] for call in calls["delete"]] == [77]


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_same_state_missing_comment_races_are_benign(tmp_path: Path) -> None:
    identity = _comment_identity("base-1", "head-1")
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="1",
        analysis_incomplete=False,
        findings="same-state rerun",
        comments=[
            {
                "id": 1,
                "body": identity + "primary",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": identity + "duplicate",
                "user": {"login": "github-actions[bot]"},
            },
        ],
        not_found_comment_ids=[1, 2],
        created_comment_id=99,
    )

    assert calls["pullsGet"] == 2
    assert [call["comment_id"] for call in calls["update"]] == [1]
    assert [call["comment_id"] for call in calls["delete"]] == [2]
    assert len(calls["create"]) == 1
    assert calls["create"][0]["body"].startswith(identity)
    assert calls["debug"] == [
        "DiffGuard comment 1 disappeared before update; continuing.",
        "DiffGuard comment 2 disappeared before deletion; continuing.",
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_comment_file_is_safely_reconciled(tmp_path: Path) -> None:
    findings = "<script>alert(1)</script> & " + ("😀" * 100_000)
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="1",
        analysis_incomplete=False,
        findings=findings,
        comments=[
            {
                "id": 1,
                "body": _comment_identity("base-1", "head-1"),
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": _comment_identity("base-1", "head-1"),
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 3,
                "body": _comment_identity("base-1", "head-1"),
                "user": {"login": "contributor"},
            },
        ],
    )

    assert calls["pullsGet"] == 2
    assert calls["graphql"] == 1
    assert calls["paginate"] == 1
    assert calls["create"] == []
    assert calls["delete"] == [{"owner": "owner", "repo": "repo", "comment_id": 2}]
    assert len(calls["update"]) == 1
    update = calls["update"][0]
    assert update["comment_id"] == 1
    body = update["body"]
    assert isinstance(body, str)
    assert body.startswith(_comment_identity("base-1", "head-1"))
    assert "<sub>Analyzed <code>base-1...head-1</code>" in body
    assert len(body.encode("utf-8")) <= 60_000
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "DiffGuard output truncated" in body


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_comment_visibly_escapes_controls_without_mutating_source_file(
    tmp_path: Path,
) -> None:
    unsafe = "\x00\x1b\x85\u061c\u200e\u202e\u2066\ufeff\U000e0020"
    findings = f"safe\ttext\ncontrols:{unsafe}:end"
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="1",
        analysis_incomplete=False,
        findings=findings,
        comments=[],
    )

    assert len(calls["create"]) == 1
    body = calls["create"][0]["body"]
    assert isinstance(body, str)
    assert "safe\ttext\ncontrols:" in body
    for character in unsafe:
        assert character not in body
    for visible_escape in (
        r"\x00",
        r"\x1b",
        r"\x85",
        r"\u061c",
        r"\u200e",
        r"\u202e",
        r"\u2066",
        r"\ufeff",
        r"\U000e0020",
    ):
        assert visible_escape in body
    assert (tmp_path / "findings.txt").read_text(encoding="utf-8") == findings


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute github-script")
def test_action_incomplete_exit_zero_updates_owned_comment(tmp_path: Path) -> None:
    calls = _run_comment_script(
        tmp_path,
        event_base="base-1",
        event_head="head-1",
        current_base="base-1",
        current_head="head-1",
        exit_code="0",
        analysis_incomplete=True,
        findings='{"warnings":[{"message":"broken.py: <parse gap>"}]}',
        comments=[
            {
                "id": 1,
                "body": _comment_identity("base-1", "head-1") + "old findings",
                "user": {"login": "github-actions[bot]"},
            },
            {
                "id": 2,
                "body": _comment_identity("base-1", "head-1") + "duplicate",
                "user": {"login": "github-actions[bot]"},
            },
        ],
    )

    assert calls["pullsGet"] == 2
    assert calls["create"] == []
    assert calls["delete"] == [{"owner": "owner", "repo": "repo", "comment_id": 2}]
    assert len(calls["update"]) == 1
    body = calls["update"][0]["body"]
    assert body.startswith(_comment_identity("base-1", "head-1"))
    assert "<sub>Analyzed <code>base-1...head-1</code>" in body
    assert "Incomplete Analysis" in body
    assert "Do not treat this run as a clean review" in body
    assert "broken.py: &lt;parse gap&gt;" in body
