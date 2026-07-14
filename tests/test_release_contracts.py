"""Static checks for release, CI, documentation, and closeout contracts."""

from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).parents[1]
PUBLISHED_GUIDE_PAGES = (
    "index.md",
    "quickstart.md",
    "real-world-catches.md",
    "how-it-works.md",
    "agent-integration.md",
    "agents-md-snippet.md",
    "claude-md-snippet.md",
    "github-copilot-instructions.md",
    "cursor-rule-snippet.md",
    "schema.md",
    "architecture.md",
    "validation.md",
    "roadmap.md",
    "adoption-kit.md",
)


def _claude_closeout_hook() -> str:
    integration = (ROOT / "docs/agent-integration.md").read_text(encoding="utf-8")
    section = integration.split("## Claude Code TaskCompleted/Stop wrapper\n", 1)[1]
    match = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL)
    assert match is not None
    return match.group(1) + "\n"


def test_ci_exercises_declared_minimum_python() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    assert 'python-version: ["3.11", "3.14"]' in workflow
    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0" in workflow
    assert "actions/setup-python@v5" not in workflow
    assert "UV_PYTHON: ${{ matrix.python-version }}" in workflow
    assert '"Programming Language :: Python :: 3.14"' in pyproject
    assert "tests on Python 3.11 and 3.14" in release


def test_every_workflow_and_composite_action_pins_verified_full_sha() -> None:
    expected = {
        "actions/checkout": {
            ("11bd71901bbe5b1630ceea73d27597364c9af683", "v4.2.2"),
        },
        "extractions/setup-just": {
            ("dd310ad5a97d8e7b41793f8ef055398d51ad4de6", "v2.0.0"),
        },
        "actions/setup-python": {
            ("ece7cb06caefa5fff74198d8649806c4678c61a1", "v6.3.0"),
        },
        "astral-sh/setup-uv": {
            ("0c5e2b8115b80b4c7c5ddf6ffdd634974642d182", "v5.4.1"),
        },
        "actions/upload-artifact": {
            ("ea165f8d65b6e75b540449e92b4886f43607fa02", "v4.6.2"),
        },
        "actions/download-artifact": {
            ("d3f86a106a0bac45b974a628896c90dbdf5c8093", "v4.3.0"),
        },
        "actions/github-script": {
            ("3a2844b7e9c422d3c10d287c895573f7108da1b3", "v9.0.0"),
        },
    }
    workflow_paths = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

    for path in (*workflow_paths, ROOT / "action.yml"):
        content = path.read_text(encoding="utf-8")
        uses = re.findall(r"^\s*-?\s*uses:\s+([^@\s]+)@([^\s]+)\s+#\s+(.+)$", content, re.MULTILINE)
        assert uses, path
        for action, revision, comment in uses:
            assert re.fullmatch(r"[0-9a-f]{40}", revision), (path, action, revision)
            assert (revision, comment) in expected[action], (path, action, revision, comment)


def test_every_setup_uv_step_pins_the_tool_binary_version() -> None:
    workflows = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    )
    setup_uv = "astral-sh/setup-uv@0c5e2b8115b80b4c7c5ddf6ffdd634974642d182 # v5.4.1"

    assert workflows.count(setup_uv) == 6
    assert workflows.count('version: "0.11.19"') == workflows.count(setup_uv)
    assert len(re.findall(setup_uv + r"\n\s+with:\n\s+version: \"0\.11\.19\"", workflows)) == 6


def test_workflows_minimize_token_permissions_and_checkout_credentials() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    docs = (ROOT / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    publish = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")

    assert "permissions: {}" in ci
    assert "  check:\n" in ci
    assert "    permissions:\n      contents: read\n" in ci

    assert "permissions: {}" in docs
    assert "  deploy:\n" in docs
    assert "    permissions:\n      contents: write\n" in docs
    assert "Deploy docs with an ephemeral Git credential" in docs
    assert "GH_TOKEN: ${{ github.token }}" in docs
    assert 'GIT_CONFIG_COUNT: "2"' in docs
    assert "GIT_CONFIG_KEY_0: credential.helper" in docs
    assert 'GIT_CONFIG_VALUE_0: ""' in docs
    assert "GIT_CONFIG_KEY_1: credential.helper" in docs
    assert "GIT_CONFIG_VALUE_1:" in docs
    assert "git config" not in docs

    assert "permissions: {}" in publish
    build = publish.split("  build:\n", 1)[1].split("  test-minimum:\n", 1)[0]
    minimum = publish.split("  test-minimum:\n", 1)[1].split("  publish-to-testpypi:\n", 1)[0]
    assert "    permissions:\n      contents: read\n" in build
    assert "    permissions:\n      contents: read\n" in minimum
    assert publish.count("      id-token: write") == 2
    assert "contents: write" not in publish

    checkout_paths = (
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/workflows/docs.yml",
        ROOT / ".github/workflows/publish-to-pypi.yml",
        ROOT / "README.md",
        ROOT / "docs/agent-integration.md",
        ROOT / "docs/llms-ctx.txt",
        ROOT / "examples/diffguard-workflow.yml",
    )
    for path in checkout_paths:
        workflow = path.read_text(encoding="utf-8")
        assert workflow.count("uses: actions/checkout@") == workflow.count(
            "persist-credentials: false"
        ), path

    example = (ROOT / "examples/diffguard-workflow.yml").read_text(encoding="utf-8")
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2" in example
    assert "actions/checkout@v4" not in example


def test_comment_enabled_example_skips_writes_for_read_only_pr_tokens() -> None:
    workflow = (ROOT / "examples/diffguard-workflow.yml").read_text(encoding="utf-8")
    integration = (ROOT / "docs/agent-integration.md").read_text(encoding="utf-8")

    assert "- id: diffguard\n        uses: ostehost/diffguard@<full-commit-sha>" in workflow
    assert "github.event.pull_request.head.repo.full_name == github.repository" in workflow
    assert "github.actor != 'dependabot[bot]'" in workflow
    assert 'post-comment: "true"' not in workflow
    assert "without attempting a comment API write" in integration
    for output in ("findings", "findings-truncated", "analysis-incomplete", "exit-code"):
        assert f"`steps.diffguard.outputs.{output}`" in integration


def test_documented_claude_hook_is_valid_bash_and_fails_closed(tmp_path: Path) -> None:
    hook = _claude_closeout_hook()
    subprocess.run(["bash", "-n"], input=hook, text=True, check=True)
    assert re.search(r"(?<![A-Za-z0-9_-])python(?:\s|$)", hook) is None
    hook_path = tmp_path / "closeout-hook.sh"
    hook_path.write_text(hook, encoding="utf-8")

    runner = tmp_path / ".agents" / "skills" / "diffguard-closeout" / "scripts" / "run_review.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        """\
import json
import os

print("DiffGuard review artifact: " + json.dumps(os.environ["FAKE_REVIEW_PATH"]))
print("DiffGuard stderr artifact: " + json.dumps(os.environ["FAKE_STDERR_PATH"]))
raise SystemExit(int(os.environ.get("FAKE_RUNNER_RC", "0")))
""",
        encoding="utf-8",
    )
    review = tmp_path / "review.json"
    stderr = tmp_path / "review.stderr"
    stderr.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "FAKE_REVIEW_PATH": str(review),
        "FAKE_STDERR_PATH": str(stderr),
    }

    complete = {"status": "ok", "warnings": [], "stats": {"parse_errors": 0}}
    review.write_text(json.dumps(complete), encoding="utf-8")
    result = subprocess.run(
        ["bash", str(hook_path)],
        cwd=tmp_path,
        env=env,
        input='{"stop_hook_active": false}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    incomplete_payloads = (
        {**complete, "status": "error"},
        {**complete, "warnings": ["parse gap"]},
        {**complete, "stats": {"parse_errors": 1}},
        {**complete, "stats": {"parse_errors": False}},
    )
    for payload in incomplete_payloads:
        review.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run(
            ["bash", str(hook_path)],
            cwd=tmp_path,
            env=env,
            input='{"stop_hook_active": false}',
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 2
        assert "clean result is invalid or incomplete" in result.stderr

    review.write_text("{not valid json", encoding="utf-8")
    result = subprocess.run(
        ["bash", str(hook_path)],
        cwd=tmp_path,
        env=env,
        input='{"stop_hook_active": false}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "clean result is invalid or incomplete" in result.stderr

    finding_env = {**env, "FAKE_RUNNER_RC": "1"}
    result = subprocess.run(
        ["bash", str(hook_path)],
        cwd=tmp_path,
        env=finding_env,
        input='{"stop_hook_active": false}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "findings require resolution" in result.stderr

    result = subprocess.run(
        ["bash", str(hook_path)],
        cwd=tmp_path,
        env={**env, "FAKE_REVIEW_PATH": str(tmp_path / "missing.json")},
        input='{"stop_hook_active": true}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0


def test_oidc_publish_jobs_use_pinned_uv_trusted_publishing_without_container_action() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")
    oidc_jobs = workflow.split("  publish-to-testpypi:", 1)[1].split("  smoke-test:", 1)[0]

    assert oidc_jobs.count("id-token: write") == 2
    assert (
        oidc_jobs.count(
            "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0"
        )
        == 2
    )
    assert (
        oidc_jobs.count("astral-sh/setup-uv@0c5e2b8115b80b4c7c5ddf6ffdd634974642d182 # v5.4.1") == 2
    )
    assert oidc_jobs.count('version: "0.11.19"') == 2
    assert oidc_jobs.count("uv publish --trusted-publishing always") == 2
    assert (
        "uv publish --trusted-publishing always "
        "--publish-url https://test.pypi.org/legacy/ dist/*" in oidc_jobs
    )
    assert "uv publish --trusted-publishing always dist/*" in oidc_jobs
    assert "pypa/gh-action-pypi-publish" not in workflow
    assert "docker://ghcr.io/pypa/gh-action-pypi-publish" not in workflow
    assert not re.search(r"uses:\s+[^\s]+@(v\d+|release/)", oidc_jobs)


def test_ci_self_review_fetches_merge_base_history_and_rejects_incomplete_analysis() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "0000000000000000000000000000000000000000" not in workflow
    assert "${BEFORE_SHA//0/}" in workflow
    refresh = workflow.split("      - name: Refresh default branch for self-review\n", 1)[1].split(
        "      - uses: extractions/setup-just@", 1
    )[0]
    assert "GH_TOKEN: ${{ github.token }}" in refresh
    assert 'GIT_CONFIG_COUNT: "2"' in refresh
    assert 'GIT_CONFIG_VALUE_0: ""' in refresh
    assert "git fetch --no-tags origin" in refresh
    assert "uv " not in refresh
    assert workflow.index("Refresh default branch for self-review") < workflow.index("uv sync")
    assert '"+refs/heads/$DEFAULT_BRANCH:refs/remotes/origin/$DEFAULT_BRANCH"' in workflow
    assert 'git fetch origin "$DEFAULT_BRANCH" --depth=1' not in workflow
    assert "ReviewEnvelope.model_validate_json" in workflow
    assert 'envelope.status == "ok"' in workflow
    assert "envelope.error is None" in workflow
    assert "not envelope.warnings" in workflow
    assert "envelope.stats.parse_errors == 0" in workflow


def test_publish_requires_tag_metadata_and_runtime_versions_to_match() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")

    assert "TAG_VERSION" in workflow
    assert "PKG_VERSION" in workflow
    assert "RUNTIME_VERSION" in workflow
    assert "diffguard.__version__" in workflow
    assert 'pip install "diffguard==$VERSION"' in workflow


def test_publish_requires_tagged_commit_to_be_reachable_from_default_branch() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")
    build = workflow.split("  build:\n", 1)[1].split("  test-minimum:\n", 1)[0]

    assert "Validate tag ancestry from the default branch" in build
    assert "RELEASE_BRANCH: ${{ github.event.repository.default_branch }}" in build
    assert "GH_TOKEN: ${{ github.token }}" in build
    assert 'GIT_CONFIG_COUNT: "2"' in build
    assert 'GIT_CONFIG_VALUE_0: ""' in build
    assert '"+refs/heads/$RELEASE_BRANCH:refs/remotes/origin/$RELEASE_BRANCH"' in build
    assert (
        'git merge-base --is-ancestor "${GITHUB_SHA}^{commit}" '
        '"refs/remotes/origin/$RELEASE_BRANCH"' in build
    )
    assert "Tagged commit is not reachable from origin/$RELEASE_BRANCH" in build
    assert build.index("Validate tag ancestry from the default branch") < build.index(
        "Build package"
    )


def test_publish_gates_on_minimum_python_and_smokes_both_distribution_formats() -> None:
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")

    assert "test-minimum:" in workflow
    assert 'UV_PYTHON: "3.11"' in workflow
    assert 'python-version: "3.11"' in workflow
    assert workflow.count('python-version: "3.14"') == 2
    assert workflow.count("needs: [build, test-minimum]") == 2
    assert "Verify built wheel and sdist in isolation" in workflow
    assert "wheels=(dist/*.whl)" in workflow
    assert "sdists=(dist/*.tar.gz)" in workflow
    assert '"${#wheels[@]}" -ne 1' in workflow
    assert '"${#sdists[@]}" -ne 1' in workflow
    assert 'uv run python -m venv "$verify_dir/wheel-venv"' in workflow
    assert 'uv run python -m venv "$verify_dir/sdist-venv"' in workflow
    assert '-m pip install --no-cache-dir "${wheels[0]}"' in workflow
    assert '-m pip install --no-cache-dir "${sdists[0]}"' in workflow
    assert workflow.count('"diffguard, version $version"') == 2
    assert workflow.index("Verify built wheel and sdist in isolation") < workflow.index(
        "Upload dist artifacts"
    )


def test_pep517_build_backend_is_exactly_pinned() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '[build-system]\nrequires = ["hatchling==1.31.0"]' in pyproject
    assert 'requires = ["hatchling"]' not in pyproject


def test_release_changelog_heading_has_an_iso_date() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(r"^## \[0\.2\.0\] - (\d{4}-\d{2}-\d{2})$", changelog, re.MULTILINE)

    assert match is not None
    assert date.fromisoformat(match.group(1)).isoformat() == match.group(1)


def test_review_schema_migration_names_the_published_predecessor() -> None:
    schema_docs = (ROOT / "docs/schema.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    schema_source = (ROOT / "src/diffguard/schema.py").read_text(encoding="utf-8")

    assert "Migration from published review `0.1.0`" in schema_docs
    assert "PyPI `0.1.3` emits review schema `0.1.0`" in schema_docs
    assert "Migration from review `1.0.0` to `1.1.0`" in schema_docs
    assert "ReviewFinding.source_file" in schema_docs
    assert "from published `0.1.0`" in changelog
    assert 'Summarize JSON migrates from published `schema_version: "1.1"` to `"2.0"`' in changelog
    assert "`SymbolChange.breaking` is now tri-state" in changelog
    assert "published 0.1.0 and the unreleased 0.2.0" in schema_source
    assert 'version: Literal["1.1.0"] = "1.1.0"' in schema_source


def test_release_docs_match_optional_parallel_testpypi_workflow() -> None:
    release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/publish-to-pypi.yml").read_text(encoding="utf-8")

    assert "optional TestPyPI and production PyPI publish in parallel" in release
    assert "Production does not wait for TestPyPI" in release
    assert "needs: [build, test-minimum]  # TestPyPI is optional" in workflow


def test_release_verification_is_isolated_exact_and_asserted() -> None:
    release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    assert 'verify_dir="$(mktemp -d)"' in release
    assert 'python3 -m venv "$verify_dir/venv"' in release
    assert '--no-cache-dir "diffguard==$VERSION"' in release
    assert 'test "$("$verify_dir/venv/bin/diffguard" --version)"' in release
    assert '"diffguard, version $VERSION"' in release
    assert "pip install diffguard && diffguard --version" not in release
    assert "  pip install diffguard\n" not in release


def test_release_movement_is_explicitly_authorized_scoped_and_atomic() -> None:
    release = (ROOT / "RELEASE.md").read_text(encoding="utf-8")

    assert "does not grant commit, tag, push, or publish authority" in release
    assert "default branch (currently `main`) is the protected release branch" in release
    assert "workflow code cannot prove that external branch protection is enabled" in release
    assert "git push --atomic origin main v0.2.0" in release
    assert "git push origin main --tags" not in release


def test_node24_action_runner_floor_is_documented() -> None:
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    public_contracts = (
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "RELEASE.md",
        ROOT / "docs" / "agent-integration.md",
        ROOT / "docs" / "llms-ctx.txt",
    )
    floor = "Self-hosted Action consumers require GitHub Actions Runner v2.327.1 or newer"
    hosted = "GitHub-hosted runners are managed by GitHub"

    assert "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0" in action
    assert "setup-python v6 uses Node 24" in action
    assert "Runner v2.327.1+" in action
    for path in public_contracts:
        content = " ".join(path.read_text(encoding="utf-8").split())
        assert floor in content, path
        assert hosted in content, path


def test_public_release_wording_is_durable_before_and_after_publication() -> None:
    public_files = (
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        ROOT / "docs" / "index.md",
        ROOT / "docs" / "quickstart.md",
        ROOT / "docs" / "roadmap.md",
        ROOT / "docs" / "llms-ctx.txt",
    )
    transient_claims = (
        "is not published",
        "not yet on PyPI",
        "currently serves `0.1.3`",
        "still exposes `0.1.3`",
        "Unreleased recovery candidate",
    )

    for path in public_files:
        content = path.read_text(encoding="utf-8")
        for claim in transient_claims:
            assert claim not in content, (path, claim)

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    assert '"diffguard>=0.2.0,<0.3"' in readme
    assert '"diffguard>=0.2.0,<0.3"' in quickstart


def test_llms_context_builder_includes_every_mkdocs_navigation_page() -> None:
    builder = (ROOT / "scripts/build-llms-ctx.sh").read_text(encoding="utf-8")

    assert all(page in builder for page in PUBLISHED_GUIDE_PAGES)
    assert "Published Guide Context" in builder


def test_generated_llms_context_matches_published_guide_sources() -> None:
    expected = "# DiffGuard — Published Guide Context\n\n"
    for page in PUBLISHED_GUIDE_PAGES:
        source = (ROOT / "docs" / page).read_text(encoding="utf-8")
        expected += f"---\n# Source: {page}\n\n{source}\n"

    generated = (ROOT / "docs" / "llms-ctx.txt").read_text(encoding="utf-8")
    assert generated == expected


def test_closeout_skill_runs_corpus_gate() -> None:
    skill = (ROOT / ".agents/skills/diffguard-closeout/SKILL.md").read_text(encoding="utf-8")

    assert "just validate-corpus" in skill


def test_closeout_review_artifacts_are_private_unique_and_owned() -> None:
    skill = (ROOT / ".agents/skills/diffguard-closeout/SKILL.md").read_text(encoding="utf-8")
    integration = (ROOT / "docs/agent-integration.md").read_text(encoding="utf-8")
    generated_context = (ROOT / "docs/llms-ctx.txt").read_text(encoding="utf-8")
    guidance = (skill, integration, generated_context)
    runner_path = ROOT / ".agents" / "skills" / "diffguard-closeout" / "scripts" / "run_review.py"
    runner = runner_path.read_text(encoding="utf-8")
    audited_paths = (
        ROOT / "README.md",
        *sorted((ROOT / ".agents").rglob("*.md")),
        *sorted((ROOT / "docs").glob("*.md")),
        ROOT / "docs/llms-ctx.txt",
        *sorted((ROOT / "scripts").glob("*.py")),
        *sorted((ROOT / "scripts").glob("*.sh")),
    )

    fixed_review_path = "/tmp/" + "diffguard-review.json"
    for path in audited_paths:
        assert fixed_review_path not in path.read_text(encoding="utf-8"), path

    for content in guidance:
        assert ".agents/skills/diffguard-closeout/scripts/run_review.py" in content
        assert "--timeout-seconds 300" in content
        assert "--max-output-bytes 10485760" in content
        assert "0600" in content
        assert "retain" in content.lower()
        assert "cleanup" in content.lower()
        assert "owner" in content.lower()
        assert not re.search(r"\brm\s+-[^\n]*[?*\[]", content)

    assert "_DEFAULT_TIMEOUT_SECONDS = 300.0" in runner
    assert "_DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024" in runner
    assert "tempfile.mkstemp" in runner
    assert "os.chmod(raw_path, 0o600)" in runner
