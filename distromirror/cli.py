"""CLI for building distro mirror images and managing repo sync + sessions."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from . import __version__

CONFIG_FILE = "config.json"
SESSIONS_FILE = "sessions.json"

APT_IDS = {
    "debian",
    "ubuntu",
    "kali",
    "linuxmint",
    "pop",
    "raspbian",
}
DNF_IDS = {
    "fedora",
    "centos",
    "rhel",
    "rocky",
    "almalinux",
}
APK_IDS = {"alpine"}
PACMAN_IDS = {"arch", "manjaro"}
ZYPPER_IDS = {"opensuse", "sles", "suse"}

DEFAULT_TRIM = {
    "apt": ["man-db", "manpages", "info", "doc-base"],
    "dnf": ["man-db", "man-pages", "info"],
    "apk": ["mandoc"],
    "pacman": ["man-db", "man-pages", "info"],
    "zypper": ["man", "info"],
}


def home_root() -> Path:
    env_root = os.environ.get("DISTRO_MIRROR_HOME")
    if env_root:
        return Path(env_root).expanduser()
    return Path.home() / ".local" / "share" / "distro-mirror"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def config_path() -> Path:
    return home_root() / CONFIG_FILE


def sessions_path() -> Path:
    return home_root() / SESSIONS_FILE


def parse_os_release(path: Path = Path("/etc/os-release")) -> dict:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def detect_package_manager(os_id: str | None) -> str:
    if not os_id:
        return "unknown"
    if os_id in APT_IDS:
        return "apt"
    if os_id in DNF_IDS:
        return "dnf"
    if os_id in APK_IDS:
        return "apk"
    if os_id in PACMAN_IDS:
        return "pacman"
    if os_id in ZYPPER_IDS:
        return "zypper"
    return "unknown"


def detect_base_image(os_id: str | None, version_id: str | None) -> str:
    if os_id == "debian" and version_id:
        return f"debian:{version_id}-slim"
    if os_id == "ubuntu" and version_id:
        return f"ubuntu:{version_id}"
    if os_id == "kali":
        return "kalilinux/kali-rolling"
    if os_id == "fedora" and version_id:
        return f"fedora:{version_id}"
    if os_id == "arch":
        return "archlinux:base"
    if os_id == "alpine" and version_id:
        return f"alpine:{version_id}"
    if os_id:
        return f"{os_id}:latest"
    return "ubuntu:latest"


def read_trim_file(path: Path | None) -> list[str]:
    if not path:
        return []
    if not path.exists():
        raise FileNotFoundError(f"trim file not found: {path}")
    packages: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        packages.append(stripped)
    return packages


def split_trim_args(values: list[str]) -> list[str]:
    packages: list[str] = []
    for value in values:
        if not value:
            continue
        for chunk in value.split(","):
            trimmed = chunk.strip()
            if trimmed:
                packages.append(trimmed)
    return packages


def merge_trim_packages(
    pkg_manager: str, extra: list[str], trim_file: Path | None
) -> list[str]:
    packages: list[str] = []
    defaults = DEFAULT_TRIM.get(pkg_manager, [])
    packages.extend(defaults)
    packages.extend(split_trim_args(extra))
    packages.extend(read_trim_file(trim_file))
    seen: set[str] = set()
    unique: list[str] = []
    for pkg in packages:
        if pkg in seen:
            continue
        unique.append(pkg)
        seen.add(pkg)
    return unique


def build_plan(args: argparse.Namespace) -> dict:
    os_release = parse_os_release()
    os_id = os_release.get("ID")
    version_id = os_release.get("VERSION_ID")
    pkg_manager = detect_package_manager(os_id)
    base_image = detect_base_image(os_id, version_id)
    trim_packages = merge_trim_packages(pkg_manager, args.trim, args.trim_file)
    return {
        "detected_os": {
            "id": os_id,
            "version_id": version_id,
            "pretty_name": os_release.get("PRETTY_NAME"),
        },
        "base_image": base_image,
        "package_manager": pkg_manager,
        "trim_packages": trim_packages,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def render_base_dockerfile(plan: dict) -> str:
    base_image = plan["base_image"]
    trim_packages = " ".join(plan["trim_packages"])
    pkg_manager = plan["package_manager"]
    lines: list[str] = [
        f"ARG BASE_IMAGE={base_image}",
        "FROM ${BASE_IMAGE}",
        f"ARG TRIM_PACKAGES=\"{trim_packages}\"",
        "SHELL [\"/bin/sh\", \"-c\"]",
    ]
    if pkg_manager == "apt":
        lines.extend(
            [
                "RUN apt-get update \\",
                "    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \\",
                "    && if [ -n \"$TRIM_PACKAGES\" ]; then apt-get purge -y $TRIM_PACKAGES; fi \\",
                "    && apt-get autoremove -y \\",
                "    && rm -rf /var/lib/apt/lists/*",
            ]
        )
    elif pkg_manager == "dnf":
        lines.extend(
            [
                "RUN dnf -y install ca-certificates git openssh-clients \\",
                "    && if [ -n \"$TRIM_PACKAGES\" ]; then dnf -y remove $TRIM_PACKAGES; fi \\",
                "    && dnf clean all",
            ]
        )
    elif pkg_manager == "apk":
        lines.extend(
            [
                "RUN apk add --no-cache ca-certificates git openssh \\",
                "    && if [ -n \"$TRIM_PACKAGES\" ]; then apk del $TRIM_PACKAGES; fi",
            ]
        )
    elif pkg_manager == "pacman":
        lines.extend(
            [
                "RUN pacman -Sy --noconfirm ca-certificates git openssh \\",
                "    && if [ -n \"$TRIM_PACKAGES\" ]; then pacman -Rns --noconfirm $TRIM_PACKAGES; fi \\",
                "    && pacman -Scc --noconfirm",
            ]
        )
    elif pkg_manager == "zypper":
        lines.extend(
            [
                "RUN zypper --non-interactive install ca-certificates git openssh \\",
                "    && if [ -n \"$TRIM_PACKAGES\" ]; then zypper --non-interactive remove $TRIM_PACKAGES; fi \\",
                "    && zypper clean -a",
            ]
        )
    else:
        lines.append("# Package manager not mapped; trimming skipped.")
    lines.append("")
    return "\n".join(lines)


def render_mirror_dockerfile(plan: dict) -> str:
    lines = render_base_dockerfile(plan).splitlines()
    lines.extend(
        [
            "COPY home.bundle /mirror/home.bundle",
            "COPY mirror-entrypoint.sh /usr/local/bin/mirror-entrypoint.sh",
            "RUN chmod +x /usr/local/bin/mirror-entrypoint.sh",
            "WORKDIR /mirror",
            "ENTRYPOINT [\"/usr/local/bin/mirror-entrypoint.sh\"]",
            "CMD [\"/bin/sh\"]",
            "",
        ]
    )
    return "\n".join(lines)


def write_plan(context: Path, plan: dict, name: str) -> None:
    context.mkdir(parents=True, exist_ok=True)
    plan_path = context / name
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def run_command(cmd: list[str], dry_run: bool = False) -> int:
    if dry_run:
        print(shlex.join(cmd))
        return 0
    result = subprocess.run(cmd, check=False)
    return result.returncode


def cmd_image_plan(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    if args.format == "json":
        print(json.dumps(plan, indent=2))
        return 0
    print(f"Base image: {plan['base_image']}")
    print(f"Package manager: {plan['package_manager']}")
    print("Trim packages:")
    for pkg in plan["trim_packages"]:
        print(f"  - {pkg}")
    return 0


def cmd_image_build(args: argparse.Namespace) -> int:
    plan = build_plan(args)
    context = Path(args.context)
    context.mkdir(parents=True, exist_ok=True)
    dockerfile = context / "Dockerfile"
    dockerfile.write_text(render_base_dockerfile(plan), encoding="utf-8")
    write_plan(context, plan, "mirror-plan.json")
    if not args.build:
        print(f"Wrote Dockerfile to {dockerfile}")
        return 0
    if not shutil.which("docker"):
        print("docker is not installed or not in PATH", file=sys.stderr)
        return 1
    tag = args.image_tag or "distro-mirror:base"
    cmd = ["docker", "build", "-t", tag]
    for build_arg in args.build_arg:
        cmd.extend(["--build-arg", build_arg])
    cmd.append(str(context))
    return run_command(cmd)


def cmd_repo_init(args: argparse.Namespace) -> int:
    repo_path = Path(args.path).expanduser().resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "init"]
    if not args.working:
        cmd.append("--bare")
    cmd.append(str(repo_path))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        return result.returncode
    config = load_json(config_path())
    config["home_repo_path"] = str(repo_path)
    config["home_repo_bare"] = not args.working
    save_json(config_path(), config)
    print(f"Initialized home repo at {repo_path}")
    return 0


def cmd_repo_sync(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    mirror = Path(args.mirror).expanduser().resolve()
    if not source.exists():
        print(f"source repo not found: {source}", file=sys.stderr)
        return 1
    if not mirror.exists():
        print(f"mirror repo not found: {mirror}", file=sys.stderr)
        return 1
    commands: list[list[str]] = []
    if args.direction in {"push", "both"}:
        commands.append(["git", "-C", str(source), "push", "--mirror", str(mirror)])
    if args.direction in {"pull", "both"}:
        commands.append(["git", "-C", str(source), "fetch", "--prune", str(mirror)])
    for cmd in commands:
        rc = run_command(cmd, dry_run=args.dry_run)
        if rc != 0:
            return rc
    return 0


def cmd_repo_bundle(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve() if args.repo else None
    if not repo:
        config = load_json(config_path())
        repo_str = config.get("home_repo_path")
        if repo_str:
            repo = Path(repo_str)
    if not repo or not repo.exists():
        print("repo not found; pass --repo or run repo init", file=sys.stderr)
        return 1
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "-C", str(repo), "bundle", "create", str(output), "--all"]
    return run_command(cmd, dry_run=args.dry_run)


def cmd_repo_mirror_build(args: argparse.Namespace) -> int:
    context = Path(args.context).expanduser().resolve()
    context.mkdir(parents=True, exist_ok=True)
    plan = build_plan(args)
    bundle_path = Path(args.bundle).expanduser().resolve() if args.bundle else None
    repo_path = Path(args.repo).expanduser().resolve() if args.repo else None
    if not bundle_path and not repo_path:
        config = load_json(config_path())
        repo_str = config.get("home_repo_path")
        if repo_str:
            repo_path = Path(repo_str)
    if bundle_path:
        shutil.copy2(bundle_path, context / "home.bundle")
    elif repo_path and repo_path.exists():
        bundle_out = context / "home.bundle"
        cmd = ["git", "-C", str(repo_path), "bundle", "create", str(bundle_out), "--all"]
        rc = run_command(cmd, dry_run=args.dry_run)
        if rc != 0:
            return rc
    else:
        print("bundle or repo required for mirror-build", file=sys.stderr)
        return 1
    dockerfile = context / "Dockerfile"
    dockerfile.write_text(render_mirror_dockerfile(plan), encoding="utf-8")
    entrypoint = context / "mirror-entrypoint.sh"
    entrypoint.write_text(
        "#!/bin/sh\nset -e\n"
        "if [ ! -d /mirror/home_repo.git ]; then\n"
        "  git clone --mirror /mirror/home.bundle /mirror/home_repo.git\n"
        "fi\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    write_plan(context, plan, "mirror-plan.json")
    if not args.build:
        print(f"Wrote mirror Dockerfile to {dockerfile}")
        return 0
    if not shutil.which("docker"):
        print("docker is not installed or not in PATH", file=sys.stderr)
        return 1
    tag = args.image_tag or "distro-mirror:mirror"
    cmd = ["docker", "build", "-t", tag, str(context)]
    return run_command(cmd, dry_run=args.dry_run)


def cmd_repo_mirror_run(args: argparse.Namespace) -> int:
    tag = args.image_tag or "distro-mirror:mirror"
    cmd = ["docker", "run", "--rm", "-it"]
    if args.name:
        cmd.extend(["--name", args.name])
    cmd.append(tag)
    if not shutil.which("docker"):
        print("docker is not installed or not in PATH", file=sys.stderr)
        return 1
    return run_command(cmd, dry_run=not args.run)


def load_sessions() -> list[dict]:
    path = sessions_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_sessions(entries: list[dict]) -> None:
    sessions_path().parent.mkdir(parents=True, exist_ok=True)
    sessions_path().write_text(
        json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8"
    )


def cmd_session_create(args: argparse.Namespace) -> int:
    sessions = load_sessions()
    if any(entry["label"] == args.label for entry in sessions):
        print(f"session label already exists: {args.label}", file=sys.stderr)
        return 1
    entry = {
        "label": args.label,
        "type": args.type,
        "target": args.target,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    sessions.append(entry)
    save_sessions(sessions)
    print(f"Saved session {args.label}")
    return 0


def cmd_session_list(_: argparse.Namespace) -> int:
    sessions = load_sessions()
    if not sessions:
        print("No sessions found")
        return 0
    print("LABEL\tTYPE\tTARGET")
    for entry in sessions:
        print(f"{entry['label']}\t{entry['type']}\t{entry['target']}")
    return 0


def session_command(entry: dict, client: str | None) -> list[str]:
    if entry["type"] == "ssh":
        return ["ssh", entry["target"]]
    if entry["type"] == "rdp":
        if client:
            return [client, entry["target"]]
        return ["xfreerdp", f"/v:{entry['target']}", "/dynamic-resolution"]
    if entry["type"] == "vnc":
        return [client or "vncviewer", entry["target"]]
    return [client or "sh"]


def cmd_session_open(args: argparse.Namespace) -> int:
    sessions = load_sessions()
    entry = next((item for item in sessions if item["label"] == args.label), None)
    if not entry:
        print(f"session not found: {args.label}", file=sys.stderr)
        return 1
    cmd = session_command(entry, args.client)
    if args.run:
        return run_command(cmd, dry_run=False)
    print(shlex.join(cmd))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="distro-mirror",
        description="Build trimmed distro images and manage repo mirrors/sessions.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="group", required=True)

    image = subparsers.add_parser("image", help="Build distro Docker images")
    image_sub = image.add_subparsers(dest="command", required=True)

    image_plan = image_sub.add_parser("plan", help="Preview image plan")
    image_plan.add_argument("--trim", action="append", default=[])
    image_plan.add_argument("--trim-file", type=Path)
    image_plan.add_argument("--format", choices=["text", "json"], default="text")
    image_plan.set_defaults(func=cmd_image_plan)

    image_build = image_sub.add_parser("build", help="Write Dockerfile and build")
    image_build.add_argument("--context", default=".distro-mirror/build")
    image_build.add_argument("--trim", action="append", default=[])
    image_build.add_argument("--trim-file", type=Path)
    image_build.add_argument("--build", action="store_true")
    image_build.add_argument("--image-tag")
    image_build.add_argument("--build-arg", action="append", default=[])
    image_build.set_defaults(func=cmd_image_build)

    repo = subparsers.add_parser("repo", help="Manage home repo mirrors")
    repo_sub = repo.add_subparsers(dest="command", required=True)

    repo_init = repo_sub.add_parser("init", help="Initialize a home repo")
    repo_init.add_argument("--path", required=True)
    repo_init.add_argument("--working", action="store_true")
    repo_init.set_defaults(func=cmd_repo_init)

    repo_sync = repo_sub.add_parser("sync", help="Sync source repo to mirror")
    repo_sync.add_argument("--source", required=True)
    repo_sync.add_argument("--mirror", required=True)
    repo_sync.add_argument(
        "--direction", choices=["push", "pull", "both"], default="push"
    )
    repo_sync.add_argument("--dry-run", action="store_true")
    repo_sync.set_defaults(func=cmd_repo_sync)

    repo_bundle = repo_sub.add_parser("bundle", help="Bundle a repo into a file")
    repo_bundle.add_argument("--repo")
    repo_bundle.add_argument("--output", required=True)
    repo_bundle.add_argument("--dry-run", action="store_true")
    repo_bundle.set_defaults(func=cmd_repo_bundle)

    repo_mirror_build = repo_sub.add_parser(
        "mirror-build", help="Build a mirror image with a repo bundle"
    )
    repo_mirror_build.add_argument("--repo")
    repo_mirror_build.add_argument("--bundle")
    repo_mirror_build.add_argument("--context", default=".distro-mirror/mirror-image")
    repo_mirror_build.add_argument("--build", action="store_true")
    repo_mirror_build.add_argument("--image-tag")
    repo_mirror_build.add_argument("--trim", action="append", default=[])
    repo_mirror_build.add_argument("--trim-file", type=Path)
    repo_mirror_build.set_defaults(func=cmd_repo_mirror_build)

    repo_mirror_run = repo_sub.add_parser("mirror-run", help="Run mirror image")
    repo_mirror_run.add_argument("--image-tag")
    repo_mirror_run.add_argument("--name")
    repo_mirror_run.add_argument("--run", action="store_true")
    repo_mirror_run.set_defaults(func=cmd_repo_mirror_run)

    session = subparsers.add_parser("session", help="Manage remote sessions")
    session_sub = session.add_subparsers(dest="command", required=True)

    session_create = session_sub.add_parser("create", help="Create a session")
    session_create.add_argument("--label", required=True)
    session_create.add_argument(
        "--type", required=True, choices=["ssh", "rdp", "vnc", "local"]
    )
    session_create.add_argument("--target", required=True)
    session_create.set_defaults(func=cmd_session_create)

    session_list = session_sub.add_parser("list", help="List sessions")
    session_list.set_defaults(func=cmd_session_list)

    session_open = session_sub.add_parser("open", help="Open a session")
    session_open.add_argument("--label", required=True)
    session_open.add_argument("--client")
    session_open.add_argument("--run", action="store_true")
    session_open.set_defaults(func=cmd_session_open)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
