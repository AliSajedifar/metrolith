# -*- coding: utf-8 -*-

"""
deployability.py
----------------
Assesses how easily a repository can be deployed.

Checks for:
- Dockerfile
- docker-compose.yml
- Kubernetes manifests
- build scripts (Maven, Gradle, npm, pip, go build)
- run/start scripts

This does NOT build or run anything — it only detects deploy-related files.
"""

from pathlib import Path

from modules.repository_files import iter_repository_files, read_text


# =====================================================================
# HELPER: CHECK IF FILE EXISTS ANYWHERE IN REPO
# =====================================================================
def contains_file(repo_path, filename, inventory=None):
    target = filename.lower()
    if inventory is not None:
        return any(
            record.exclusion_reason in {None, "test"}
            for record in inventory.records_for_name(target)
        )
    for path in iter_repository_files(repo_path, inventory=inventory):
        if path.name.lower() == target:
            return True
    return False


# =====================================================================
# HELPER: SEARCH FOR FILES THAT CONTAIN KEYWORDS (e.g., k8s manifests)
# =====================================================================
def contains_pattern(repo_path, extensions, keywords, inventory=None):
    """
    Scan repository for files with extensions like .yaml or .yml
    and check if they contain Kubernetes-related keywords.
    """
    repo_path = Path(repo_path)

    for path in iter_repository_files(repo_path, extensions=extensions, inventory=inventory):
        content = read_text(path, inventory=inventory)
        if content and all(k.lower() in content.lower() for k in keywords):
            return True
    return False


# =====================================================================
# MAIN DEPLOYABILITY INSPECTION FUNCTION
# =====================================================================
def assess_deployability(repo_path, inventory=None):
    """
    Returns a dictionary describing the deployability level.
    Does not attempt to build or execute anything.
    """

    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")

    dockerfile = contains_file(repo_path, "Dockerfile", inventory)
    compose = (
        contains_file(repo_path, "docker-compose.yml", inventory)
        or contains_file(repo_path, "docker-compose.yaml", inventory)
        or contains_file(repo_path, "compose.yml", inventory)
        or contains_file(repo_path, "compose.yaml", inventory)
    )
    k8s = contains_pattern(repo_path, [".yaml", ".yml"], ["apiversion", "kind:"], inventory)

    # Build tools detection
    build_tools = {
        "maven": contains_file(repo_path, "pom.xml", inventory),
        "gradle": contains_file(repo_path, "build.gradle", inventory),
        "gradle_kotlin": contains_file(repo_path, "build.gradle.kts", inventory),
        "npm": contains_file(repo_path, "package.json", inventory),
        "python": (
            contains_file(repo_path, "requirements.txt", inventory)
            or contains_file(repo_path, "pyproject.toml", inventory)
            or contains_file(repo_path, "setup.py", inventory)
        ),
        "go": contains_file(repo_path, "go.mod", inventory),
        "dotnet": contains_file(repo_path, "global.json", inventory)
        or any(path.suffix.lower() == ".sln" for path in iter_repository_files(repo_path, inventory=inventory)),
        "rust": contains_file(repo_path, "Cargo.toml", inventory),
    }

    # Run / start scripts
    run_scripts = {
        "bash_run": contains_file(repo_path, "run.sh", inventory),
        "bash_start": contains_file(repo_path, "start.sh", inventory),
        "docker_run": contains_file(repo_path, "docker-run.sh", inventory),
        "makefile": contains_file(repo_path, "Makefile", inventory),
    }

    return {
        "dockerfile": dockerfile,
        "docker_compose": compose,
        "kubernetes_manifests": k8s,
        "build_tools": build_tools,
        "run_scripts": run_scripts,
    }
