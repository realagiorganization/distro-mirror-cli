"""Step definitions for Distro Mirror CLI."""

import subprocess
import sys
from pathlib import Path

from behave import given, when, then


def run_cli(context, args, check=True):
    cmd = [sys.executable, "-m", "distromirror"] + args
    result = subprocess.run(
        cmd,
        cwd=context.repo_root,
        env=context.env,
        capture_output=True,
        text=True,
    )
    context.last_result = result
    if check and result.returncode != 0:
        raise AssertionError(
            f"Command failed: {cmd}\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    return result


@when("I build a distro image context")
def build_distro_context(context):
    context.build_context = context.temp_path / "context"
    run_cli(context, ["image", "build", "--context", str(context.build_context)])


@then("a Dockerfile is created")
def dockerfile_created(context):
    dockerfile = context.build_context / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile was not created"


@then("a mirror plan is created")
def mirror_plan_created(context):
    plan = context.build_context / "mirror-plan.json"
    assert plan.exists(), "mirror-plan.json was not created"


@when("I initialize a home repo")
def init_home_repo(context):
    context.home_repo = context.temp_path / "home_repo"
    run_cli(context, ["repo", "init", "--path", str(context.home_repo)])


@then("the home repo is bare")
def home_repo_is_bare(context):
    head = context.home_repo / "HEAD"
    objects = context.home_repo / "objects"
    assert head.exists(), "Bare repo HEAD missing"
    assert objects.exists(), "Bare repo objects missing"


@given("a sample git repo")
def sample_git_repo(context):
    context.sample_repo = context.temp_path / "sample_repo"
    context.sample_repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=context.sample_repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "bdd@example.com"],
        cwd=context.sample_repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "BDD Runner"],
        cwd=context.sample_repo,
        check=True,
    )
    sample_file = context.sample_repo / "README.md"
    sample_file.write_text("sample", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=context.sample_repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=context.sample_repo, check=True)


@when("I bundle the repository")
def bundle_repository(context):
    context.bundle_path = context.temp_path / "home.bundle"
    run_cli(
        context,
        [
            "repo",
            "bundle",
            "--repo",
            str(context.sample_repo),
            "--output",
            str(context.bundle_path),
        ],
    )


@then("the bundle file exists")
def bundle_file_exists(context):
    assert context.bundle_path.exists(), "Bundle file was not created"


@when("I create an rdp session")
def create_rdp_session(context):
    run_cli(
        context,
        [
            "session",
            "create",
            "--type",
            "rdp",
            "--target",
            "lab-host:3389",
            "--label",
            "lab-rdp",
        ],
    )


@when("I open the session")
def open_session(context):
    run_cli(context, ["session", "open", "--label", "lab-rdp"])


@then('the output includes "xfreerdp"')
def output_includes_xfreerdp(context):
    assert "xfreerdp" in context.last_result.stdout
