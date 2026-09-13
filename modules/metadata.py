# -*- coding: utf-8 -*-

"""
metadata.py
-----------
Fetches repository metadata from the GitHub REST API.

Extracted information includes:
- Repository name
- Description
- Primary language
- Stars, forks, watchers
- License type
- Open issues count
- Default branch

This helps in classification, reproducibility checks, and fact sheet generation.
"""

import json
import os
import shutil
import subprocess
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GITHUB_API = "https://api.github.com/repos/"


def _fetch_json(url):
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Metrolith/3.0.0",
            **(
                {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
                if os.environ.get("GITHUB_TOKEN")
                else {}
            ),
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError):
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            raise

        result = subprocess.run(
            [
                curl,
                "--ssl-no-revoke",
                "-L",
                "-A",
                "Metrolith/3.0.0",
                "-H",
                "Accept: application/vnd.github+json",
                url,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return json.loads(result.stdout)


def parse_github_repo(repo_url):
    """Return (owner, repo) for common GitHub HTTPS and SSH URLs."""
    normalized = repo_url.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]

    if normalized.startswith("git@github.com:"):
        path = normalized.split(":", 1)[1]
        parts = path.split("/")
    else:
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https", "ssh", "git"}:
            raise ValueError("Repository URL must use HTTPS or SSH")
        if parsed.hostname not in {"github.com", "www.github.com"}:
            raise ValueError("Only github.com repository URLs are supported")
        parts = [part for part in parsed.path.split("/") if part]

    if len(parts) != 2 or not all(parts):
        raise ValueError("Invalid repository URL")

    return parts[-2], parts[-1]


def fetch_metadata(repo_url):
    """
    Given a GitHub HTTPS/SSH URL, extract the owner and repo name,
    then fetch metadata from the GitHub REST API.

    Returns a dictionary with relevant metadata fields.
    """

    try:
        owner, repo = parse_github_repo(repo_url)
    except ValueError:
        return {
            "error": "Invalid repository URL",
            "raw_url": repo_url,
        }

    api_url = f"{GITHUB_API}{owner}/{repo}"

    try:
        data = _fetch_json(api_url)
    except HTTPError as e:
        return {
            "error": f"GitHub API responded with {e.code}",
            "raw_url": repo_url,
        }
    except (URLError, TimeoutError, OSError) as e:
        return {
            "error": f"Request failed: {e}",
            "raw_url": repo_url,
        }
    except subprocess.CalledProcessError as e:
        return {
            "error": f"GitHub API request failed: {e.stderr or e}",
            "raw_url": repo_url,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "error": f"GitHub API request timed out after {e.timeout}s",
            "raw_url": repo_url,
        }
    except json.JSONDecodeError as e:
        return {
            "error": f"Invalid JSON response: {e}",
            "raw_url": repo_url,
        }

    try:
        if not isinstance(data, dict):
            return {
                "error": "Unexpected GitHub API response",
                "raw_url": repo_url,
            }
        if data.get("message") and not data.get("name"):
            return {
                "error": data.get("message"),
                "raw_url": repo_url,
                "name": repo,
                "full_name": f"{owner}/{repo}",
                "description": None,
                "language": None,
                "stars": None,
                "forks": None,
                "watchers": None,
                "open_issues": None,
                "default_branch": None,
                "license": None,
            }

        return {
            "name": data.get("name"),
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "language": data.get("language"),
            "stars": data.get("stargazers_count"),
            "forks": data.get("forks_count"),
            "watchers": data.get("watchers_count"),
            "open_issues": data.get("open_issues_count"),
            "default_branch": data.get("default_branch"),
            "license": data.get("license", {}).get("spdx_id")
            if data.get("license")
            else None,
            "archived": data.get("archived"),
            "fork": data.get("fork"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "pushed_at": data.get("pushed_at"),
            "html_url": data.get("html_url"),
        }

    except Exception as e:
        return {
            "error": f"Metadata parsing failed: {e}",
            "raw_url": repo_url,
        }
