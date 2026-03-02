#!/usr/bin/env python3
"""Mint an Azure Storage bearer token via GitHub OIDC and Azure CLI.

This script is designed for GitHub Actions CI. It:
1. fetches a GitHub OIDC token for Azure token exchange,
2. logs into Azure CLI using the federated OIDC token,
3. requests a storage bearer token from Azure CLI,
4. writes the token to GITHUB_ENV as MATHLIB_CACHE_AZURE_BEARER_TOKEN.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

OIDC_AUDIENCE = "api://AzureADTokenExchange"
STORAGE_RESOURCE = "https://storage.azure.com/"
OUTPUT_ENV_VAR = "MATHLIB_CACHE_AZURE_BEARER_TOKEN"


def fail(message: str) -> None:
    """Print an error and exit with status 1."""

    print(message, file=sys.stderr)
    raise SystemExit(1)


def get_actions_oidc_token() -> str:
    """Fetch a GitHub Actions OIDC token for the Azure token exchange audience."""

    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not request_url or not request_token:
        fail(
            "GitHub OIDC request variables are missing. "
            "Ensure the workflow grants `permissions: id-token: write`."
        )

    parsed = urllib.parse.urlsplit(request_url)
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs.append(("audience", OIDC_AUDIENCE))
    url_with_audience = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query_pairs), parsed.fragment)
    )

    req = urllib.request.Request(url=url_with_audience, method="GET")
    req.add_header("Authorization", f"Bearer {request_token}")
    try:
        with urllib.request.urlopen(req) as resp:
            oidc_response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        fail(f"Failed to fetch GitHub OIDC token: HTTP {err.code}\\n{detail}")
    except urllib.error.URLError as err:
        fail(f"Failed to fetch GitHub OIDC token: {err.reason}")
    except json.JSONDecodeError:
        fail("GitHub OIDC response was not valid JSON.")

    oidc_token = oidc_response.get("value")
    if not isinstance(oidc_token, str) or not oidc_token.strip():
        fail("GitHub OIDC response did not include token in `value`.")
    return oidc_token.strip()


def run_cmd(args: list[str]) -> str:
    """Run a command and return stdout, failing with stderr context on error."""

    try:
        proc = subprocess.run(args, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as err:
        stderr = err.stderr.strip()
        stdout = err.stdout.strip()
        details = "\n".join(part for part in [stdout, stderr] if part)
        fail(f"Command failed: {' '.join(args)}\n{details}")
    return proc.stdout


def write_env_var(name: str, value: str) -> None:
    """Append an env var assignment to GITHUB_ENV."""

    env_path = os.environ.get("GITHUB_ENV", "").strip()
    if not env_path:
        fail("GITHUB_ENV is not set.")
    with open(env_path, "a", encoding="utf-8") as env_file:
        env_file.write(f"{name}={value}\\n")


def main() -> None:
    """Run the token mint flow and publish token to GITHUB_ENV."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    args = parser.parse_args()

    oidc_token = get_actions_oidc_token()

    run_cmd(
        [
            "az",
            "login",
            "--service-principal",
            "--username",
            args.client_id,
            "--tenant",
            args.tenant_id,
            "--federated-token",
            oidc_token,
            "--allow-no-subscriptions",
            "--output",
            "none",
        ]
    )

    bearer = run_cmd(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            STORAGE_RESOURCE,
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ]
    ).strip()

    if not bearer:
        fail("Azure CLI returned an empty access token.")

    print(f"::add-mask::{bearer}")
    write_env_var(OUTPUT_ENV_VAR, bearer)


if __name__ == "__main__":
    main()
