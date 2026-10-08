#!/usr/bin/env python3
"""Route one Codex execution task with TypeSafe Jev.

Reads a JSON object from stdin and prints one JSON object to stdout.
No prompt, response, or API key is persisted.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import urllib.error
import urllib.request
from typing import Any


TIERS = ("fast", "balanced", "strong", "long")
DEFAULT_MODELS = {
    "fast": "gpt-6-luna",
    "balanced": "gpt-6-luna",
    "strong": "gpt-6.1-sol",
    "long": "gpt-6-astra",
}
# Canonical tiers also recognize models no longer present in configured targets.
# In particular, an existing GPT-6-Sol session remains Strong after this upgrade.
CANONICAL_TIERS = {
    "gpt-6-luna": ("fast", "balanced"),
    "gpt-6-sol": ("strong",),
    "gpt-6.1-sol": ("strong",),
    "gpt-6-astra": ("long",),
}
ALLOWED_MODELS = frozenset(CANONICAL_TIERS)
DEFAULT_EFFORTS = {
    "fast": "low",
    "balanced": "medium",
    "strong": "high",
    "long": "max",
}


def emit(payload: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(exit_code)


def read_request() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, UnicodeError, OSError):
        emit({"status": "error", "reason": "invalid_input_json"}, 2)
    if not isinstance(payload, dict) or not isinstance(payload.get("task"), str):
        emit({"status": "error", "reason": "input_requires_task_string"}, 2)
    if not payload["task"].strip():
        emit({"status": "error", "reason": "empty_task"}, 2)
    for field, allowed in (
        ("risk", ("low", "normal", "high")),
        ("expected_scope", ("single-step", "contained", "multi-step", "long-running")),
    ):
        if field in payload and payload[field] not in allowed:
            emit({"status": "error", "reason": f"invalid_{field}"}, 2)
    for field in ("current_model", "current_reasoning_effort"):
        if field in payload and not isinstance(payload[field], str):
            emit({"status": "error", "reason": f"invalid_{field}"}, 2)
    return payload


def models() -> dict[str, str]:
    configured: dict[str, str] = {}
    for tier, default_model in DEFAULT_MODELS.items():
        requested = os.getenv(f"JEV_CODEX_{tier.upper()}_MODEL", default_model)
        # Keep delegated work within the supported GPT-6 model allowlist.
        configured[tier] = requested if requested in ALLOWED_MODELS else default_model
    return configured


def current_tier(
    current_model: str, configured: dict[str, str], current_effort: str = ""
) -> str | None:
    # Explicit target assignments take precedence over canonical model roles.
    # Use canonical roles only when the model is absent from all targets; do not
    # silently rewrite a caller's override (including a legacy Sol override).
    candidates = [tier for tier in TIERS if configured.get(tier) == current_model]
    if not candidates:
        candidates = list(CANONICAL_TIERS.get(current_model, ()))
    for tier in candidates:
        if DEFAULT_EFFORTS[tier] == current_effort:
            return tier
    # Fast and Balanced share a model; unknown effort must not permit a downgrade.
    return candidates[-1] if candidates else None


def apply_policy(
    request: dict[str, Any], choice: str, confidence: float, probabilities: Any
) -> dict[str, Any]:
    configured = models()
    if choice not in TIERS:
        return {"status": "keep_current", "reason": "unknown_tier"}
    if not 0.0 <= confidence <= 1.0:
        return {"status": "keep_current", "reason": "invalid_confidence"}

    selected = choice
    reasons: list[str] = []
    if request.get("risk") == "high" and TIERS.index(selected) < TIERS.index("strong"):
        selected = "strong"
        reasons.append("high_risk_floor")

    if selected == "long" and os.getenv("JEV_ROUTER_ALLOW_LONG") != "1":
        selected = "strong"
        reasons.append("long_disabled")

    # High-risk work must not fall back to a weaker current model merely
    # because Jev is uncertain. The Strong floor is the safety boundary;
    # ordinary work keeps the original fail-open behavior.
    if confidence < 0.60 and request.get("risk") != "high":
        return {
            "status": "keep_current",
            "reason": "low_confidence",
            "tier": selected,
            "recommended_model": configured[selected],
            "recommended_reasoning_effort": DEFAULT_EFFORTS[selected],
            "confidence": confidence,
            "probabilities": probabilities,
        }

    current = current_tier(
        request.get("current_model", ""), configured,
        request.get("current_reasoning_effort", ""),
    )
    if 0.60 <= confidence < 0.80 and current is not None:
        if TIERS.index(selected) < TIERS.index(current):
            return {
                "status": "keep_current",
                "reason": "medium_confidence_no_downgrade",
                "tier": selected,
                "recommended_model": configured[selected],
                "recommended_reasoning_effort": DEFAULT_EFFORTS[selected],
                "current_model": request.get("current_model"),
                "confidence": confidence,
                "probabilities": probabilities,
            }

    if confidence < 0.60 and request.get("risk") == "high":
        reasons.append("high_risk_low_confidence_floor")

    return {
        "status": "routed",
        "tier": selected,
        "model": configured[selected],
        "reasoning_effort": DEFAULT_EFFORTS[selected],
        "confidence": confidence,
        "probabilities": probabilities,
        "policy": reasons or ["jev_recommendation"],
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never forward a credential-bearing request to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect_refused", headers, fp)


def call_jev(request: dict[str, Any], timeout: float) -> tuple[str, float, Any]:
    api_key = os.getenv("TYPESAFE_API_KEY") or os.getenv("JEV_API_KEY")
    if not api_key:
        emit({"status": "error", "reason": "missing_TYPESAFE_API_KEY_or_JEV_API_KEY"}, 2)

    state = json.dumps(
        {
            "task": request["task"],
            "risk": request.get("risk", "normal"),
            "expected_scope": request.get("expected_scope", "contained"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    body = {
        "state": state,
        "model": os.getenv("JEV_MODEL", "jev-latest"),
        "questions": {
            "model_tier": {
                "type": "choice",
                "instructions": (
                    "Choose the least expensive Codex model tier that can reliably "
                    "complete this software-agent task."
                ),
                "criteria": {
                    "fast": "Clear, narrow, mechanical, repetitive, or high-volume work.",
                    "balanced": "Normal exploration, analysis, or contained development.",
                    "strong": "Ambiguous multi-step implementation, difficult debugging, or consequential work.",
                    "long": "Architecture-level, unusually long, or exceptionally complex work.",
                },
            }
        },
    }
    http_request = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(http_request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        emit({"status": "error", "reason": "jev_http_error", "http_status": exc.code}, 2)
    except urllib.error.URLError as exc:
        cause = getattr(exc, "reason", None)
        message = str(exc)
        if isinstance(cause, socket.gaierror) or any(
            marker in message.lower()
            for marker in (
                "nodename nor servname provided",
                "name or service not known",
                "temporary failure in name resolution",
            )
        ):
            emit(
                {
                    "status": "error",
                    "reason": "network_unavailable_or_sandboxed",
                    "hint": "run_this_router_process_outside_the_restricted_sandbox",
                },
                2,
            )
        emit({"status": "error", "reason": "jev_network_error"}, 2)
    except (TimeoutError, OSError):
        emit({"status": "error", "reason": "jev_timeout_or_io_error"}, 2)
    except (ValueError, UnicodeError):
        emit({"status": "error", "reason": "invalid_jev_json"}, 2)

    try:
        answer = result["answers"]["model_tier"]
        choice, confidence = answer["choice"], answer["confidence"]
        if not isinstance(choice, str) or type(confidence) not in (int, float):
            raise ValueError("invalid_choice_or_confidence")
        probabilities = answer.get("probabilities")
        if probabilities is not None and (
            not isinstance(probabilities, dict)
            or any(
                tier not in TIERS or type(value) not in (int, float)
                or not 0.0 <= value <= 1.0
                for tier, value in probabilities.items()
            )
        ):
            raise ValueError("invalid_probabilities")
        return choice, float(confidence), probabilities
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        emit({"status": "error", "reason": "invalid_jev_response"}, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--offline-choice", choices=TIERS)
    parser.add_argument("--offline-confidence", type=float)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        emit({"status": "error", "reason": "timeout_must_be_positive_and_finite"}, 2)
    request = read_request()

    if args.offline_choice is not None:
        if args.offline_confidence is None:
            emit({"status": "error", "reason": "offline_confidence_required"}, 2)
        choice, confidence, probabilities = (
            args.offline_choice,
            args.offline_confidence,
            {args.offline_choice: 1.0},
        )
    else:
        choice, confidence, probabilities = call_jev(request, args.timeout)

    emit(apply_policy(request, choice, confidence, probabilities))


if __name__ == "__main__":
    main()
