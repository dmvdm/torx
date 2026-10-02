#!/usr/bin/env python3
"""
Fetch test results from the Test Observer API and convert them to
Allure 3 result files (test-results/ directory).

Uses GET /v1/test-results, which returns one item per test result with
full execution, artefact, and build context already embedded — a natural
1-to-1 mapping to Allure test cases.

After running this script, generate the HTML report with:
  allure generate test-results -o test-report
  allure open test-report          # or: allure serve test-results

Usage:
  python export_artefact_allure_test_results.py --api-url https://<host> [options]

Authentication (choose one):
  --token TOKEN          Bearer token
  --token-env ENV_VAR    Read token from environment variable (default: TEST_OBSERVER_TOKEN)
"""

import argparse
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


# ── Status mappings ───────────────────────────────────────────────────────────

TEST_RESULT_STATUS_MAP = {
    "PASSED": "passed",
    "FAILED": "failed",
    "SKIPPED": "skipped",
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def fetch_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} fetching {url}: {body}") from exc


def fetch_all_test_results(api_url: str, token: str | None, extra_params: dict) -> list:
    """Page through GET /v1/test-results, collecting all TestResultResponseWithContext items."""
    limit = 1000
    offset = 0
    all_items: list = []

    while True:
        params = {"limit": limit, "offset": offset, **extra_params}
        qs = urllib.parse.urlencode(params, doseq=True)
        url = f"{api_url}/v1/test-results?{qs}"
        print(f"  Fetching {url}", flush=True)
        data = fetch_json(url, token)

        batch = data.get("test_results", [])
        all_items.extend(batch)
        total = data.get("count", 0)
        print(f"  Got {len(all_items)} / {total} test results", flush=True)

        if not batch or offset + limit >= total:
            break
        offset += limit

    return all_items


# ── Conversion helpers ────────────────────────────────────────────────────────

def iso_to_ms(iso_str: str | None) -> int | None:
    """ISO-8601 datetime → epoch milliseconds (Allure expects ms)."""
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def history_id(artefact_name: str, env_name: str, test_name: str) -> str:
    """Stable hash used by Allure for trend / history tracking across runs."""
    key = f"{artefact_name}::{env_name}::{test_name}"
    return hashlib.md5(key.encode()).hexdigest()


def build_allure_result(
    item: dict,
    output_dir: Path,
) -> dict:
    """Return an Allure result dict for one TestResultResponseWithContext."""

    test_result = item["test_result"]
    execution = item["test_execution"]
    artefact = item["artefact"]
    artefact_build = item["artefact_build"]
    env = execution.get("environment", {})
    exec_metadata: dict = execution.get("execution_metadata", {})

    artefact_name = artefact.get("name", "unknown")
    artefact_version = artefact.get("version", "")
    artefact_family = artefact.get("family", "")
    artefact_stage = artefact.get("stage", "")
    env_name = env.get("name", "unknown")
    env_arch = env.get("architecture", "")
    build_arch = artefact_build.get("architecture", "")
    revision = artefact_build.get("revision")
    test_plan = execution.get("test_plan", "")
    ci_link = execution.get("ci_link")
    c3_link = execution.get("c3_link")

    test_name = test_result.get("name", "unnamed")
    category = test_result.get("category", "")
    template_id = test_result.get("template_id", "")
    status = TEST_RESULT_STATUS_MAP.get(test_result.get("status", ""), "unknown")
    comment = test_result.get("comment", "")
    io_log = test_result.get("io_log", "")
    created_at = test_result.get("created_at") or execution.get("created_at")

    start_ms = iso_to_ms(created_at)

    # ── Labels ────────────────────────────────────────────────────────────────
    # Suites view hierarchy:
    #   parentSuite → artefact name  (top level)
    #   suite       → test plan      (second level)
    #   subSuite    → category       (third level; omitted when empty)
    #
    # Behaviors view hierarchy (kept for cross-referencing):
    #   epic    → artefact family (snap / charm / deb / …)
    #   feature → artefact name
    #   story   → test plan
    labels = [
        {"name": "parentSuite", "value": artefact_name},
        {"name": "suite", "value": test_plan or "default"},
        {"name": "epic", "value": artefact_family or "unknown"},
        {"name": "feature", "value": artefact_name},
        {"name": "story", "value": test_plan or "default"},
        {"name": "tag", "value": f"env:{env_name}"},
        {"name": "tag", "value": f"family:{artefact_family}"},
    ]
    if category:
        labels.append({"name": "subSuite", "value": category})
    if env_arch:
        labels.append({"name": "tag", "value": f"arch:{env_arch}"})
    if artefact_stage:
        labels.append({"name": "tag", "value": f"stage:{artefact_stage}"})
    if artefact_version:
        labels.append({"name": "tag", "value": f"version:{artefact_version}"})

    # ── Links ─────────────────────────────────────────────────────────────────
    links = []
    if ci_link:
        links.append({"name": "CI", "url": ci_link, "type": "link"})
    if c3_link:
        links.append({"name": "C3", "url": c3_link, "type": "link"})

    # ── Parameters ────────────────────────────────────────────────────────────
    # Shown in Allure's "Parameters" tab; used for grouping retries.
    parameters = [{"name": "environment", "value": env_name}]
    if env_arch:
        parameters.append({"name": "env_arch", "value": env_arch})
    if artefact_version:
        parameters.append({"name": "version", "value": artefact_version})
    if build_arch:
        parameters.append({"name": "build_arch", "value": build_arch})
    if revision is not None:
        parameters.append({"name": "revision", "value": str(revision)})
    parameters.append({"name": "execution_id", "value": str(execution["id"])})
    if created_at:
        parameters.append({"name": "date", "value": created_at[:10]})
    if template_id:
        parameters.append({"name": "template_id", "value": template_id})
    # execution_metadata is a free-form category → [value, …] dict
    for meta_key, meta_values in exec_metadata.items():
        for val in meta_values:
            parameters.append({"name": meta_key, "value": val})

    # ── Attachments ───────────────────────────────────────────────────────────
    attachments = []
    if io_log:
        attach_uuid = str(uuid.uuid4())
        attach_filename = f"{attach_uuid}-attachment.txt"
        (output_dir / attach_filename).write_text(io_log, encoding="utf-8")
        attachments.append({
            "name": "IO Log",
            "source": attach_filename,
            "type": "text/plain",
        })

    # ── Status details ────────────────────────────────────────────────────────
    status_details: dict = {}
    if comment:
        status_details["message"] = comment
    if io_log and status == "failed":
        lines = io_log.strip().splitlines()
        status_details["trace"] = "\n".join(lines[-30:]) if len(lines) > 30 else io_log

    # ── Assemble result ───────────────────────────────────────────────────────
    full_name = (
        f"{artefact_name}/{category}/{test_name}"
        if category
        else f"{artefact_name}/{test_name}"
    )
    result = {
        "uuid": str(uuid.uuid4()),
        "historyId": history_id(artefact_name, env_name, test_name),
        "name": test_name,
        "fullName": full_name,
        "status": status,
        "start": start_ms,
        "stop": start_ms,
        "labels": labels,
        "links": links,
        "parameters": parameters,
        "attachments": attachments,
    }
    if status_details:
        result["statusDetails"] = status_details

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--api-url",
        required=True,
        metavar="URL",
        help="Base URL of the Test Observer API",
    )
    p.add_argument(
        "--token",
        metavar="TOKEN",
        help="Bearer token for authentication",
    )
    p.add_argument(
        "--token-env",
        metavar="ENV_VAR",
        default="TEST_OBSERVER_TOKEN",
        help="Name of env var holding the bearer token (default: TEST_OBSERVER_TOKEN)",
    )

    # Filters forwarded to GET /v1/test-results
    p.add_argument(
        "--family",
        dest="families",
        action="append",
        metavar="FAMILY",
        help="Filter by artefact family (snap|deb|charm|image|solution). Repeatable.",
    )
    p.add_argument(
        "--artefact",
        dest="artefacts",
        action="append",
        metavar="NAME",
        help="Filter by artefact name. Repeatable.",
    )
    p.add_argument(
        "--artefact-versions",
        dest="artefact_versions",
        action="append",
        metavar="NAME",
        help="Filter by artefact versions. Repeatable.",
    )
    p.add_argument(
        "--artefact-stages",
        dest="artefact_stages",
        action="append",
        metavar="NAME",
        help="Filter by artefact stages. Repeatable.",
    )
    p.add_argument(
        "--artefact-tracks",
        dest="artefact_tracks",
        action="append",
        metavar="NAME",
        help="Filter by artefact tracks. Repeatable.",
    )
    p.add_argument(
        "--environment",
        dest="environments",
        action="append",
        metavar="ENV",
        help="Filter by environment name. Repeatable.",
    )
    p.add_argument(
        "--result-status",
        dest="test_result_statuses",
        action="append",
        metavar="STATUS",
        choices=["PASSED", "FAILED", "SKIPPED"],
        help="Filter by individual test result status. Repeatable.",
    )
    p.add_argument(
        "--execution-status",
        dest="test_execution_statuses",
        action="append",
        metavar="STATUS",
        choices=["NOT_STARTED", "IN_PROGRESS", "PASSED", "FAILED", "NOT_TESTED", "ENDED_PREMATURELY"],
        help="Filter by parent execution status. Repeatable.",
    )
    p.add_argument(
        "--test-plans",
        dest="test_plans",
        action="append",
        metavar="NAME",
        help="Filter by test plan name. Repeatable.",
    )
    p.add_argument(
        "--test-case",
        dest="test_cases",
        action="append",
        metavar="NAME",
        help="Filter by test case name. Repeatable.",
    )
    p.add_argument(
        "--template-id",
        dest="template_ids",
        action="append",
        metavar="ID",
        help="Filter by template ID. Repeatable.",
    )
    p.add_argument(
        "--from-date",
        metavar="DATETIME",
        help="Include only results from this ISO-8601 datetime (e.g. 2024-01-01T00:00:00Z).",
    )
    p.add_argument(
        "--until-date",
        metavar="DATETIME",
        help="Include only results until this ISO-8601 datetime.",
    )
    p.add_argument(
        "--latest-only",
        action="store_true",
        default=False,
        help="Only include results from the latest execution per environment/artefact/test-plan combination",
    )
    p.add_argument(
        "--output-dir",
        default="test-results",
        metavar="DIR",
        help="Directory where Allure result files will be written (default: test-results)",
    )

    return p.parse_args()


def main() -> None:
    args = parse_args()

    # Resolve auth token
    token = args.token or os.environ.get(args.token_env)

    # Build filter params for GET /v1/test-results
    extra_params: dict = {}
    if args.families:
        extra_params["families"] = args.families
    if args.artefacts:
        extra_params["artefacts"] = args.artefacts
    if args.artefact_versions:
        extra_params["artefact_versions"] = args.artefact_versions
    if args.artefact_stages:
        extra_params["artefact_stages"] = args.artefact_stages
    if args.artefact_tracks:
        extra_params["artefact_tracks"] = args.artefact_tracks
    if args.environments:
        extra_params["environments"] = args.environments
    if args.test_result_statuses:
        extra_params["test_result_statuses"] = args.test_result_statuses
    if args.test_execution_statuses:
        extra_params["test_execution_statuses"] = args.test_execution_statuses
    if args.test_plans:
        extra_params["test_plans"] = args.test_plans 
    if args.test_cases:
        extra_params["test_cases"] = args.test_cases
    if args.template_ids:
        extra_params["template_ids"] = args.template_ids
    if args.from_date:
        extra_params["from_date"] = args.from_date
    if args.until_date:
        extra_params["until_date"] = args.until_date
    if args.latest_only:
        extra_params["execution_is_latest"] = "true"

    api_url = args.api_url.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Fetching test results from {api_url} …")
    items = fetch_all_test_results(api_url, token, extra_params)
    print(f"Fetched {len(items)} test results.\n")

    for item in items:
        allure_result = build_allure_result(item, output_dir)
        result_path = output_dir / f"{allure_result['uuid']}-result.json"
        result_path.write_text(json.dumps(allure_result, indent=2), encoding="utf-8")

    print(f"\n✓ Wrote {len(items)} Allure result files to '{output_dir}/'")
    print()
    print("Next steps:")
    print(f"npx allure generate {output_dir} -o test-report")
    print( "npx allure open test-report")
    print()
    print("  # or serve directly (no static generation needed):")
    print(f"npx allure serve {output_dir}")


if __name__ == "__main__":
    main()
