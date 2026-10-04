"""Login, submit and poll a task without persisting session credentials."""

import argparse
import json
import time
from collections.abc import Callable

import httpx


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(description="Read-only task CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask")
    ask.add_argument("--as", dest="label", required=True)
    ask.add_argument("request")
    ask.add_argument("--base-url", default="http://127.0.0.1:8000")
    ask.add_argument("--timeout", type=float, default=120)
    ask.add_argument("--interval", type=float, default=1)
    args = parser.parse_args(argv)
    if args.timeout <= 0 or args.interval <= 0:
        print("Invalid timeout or interval")
        return 1
    deadline = time.monotonic() + args.timeout
    try:
        with httpx.Client(
            base_url=args.base_url,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:

            def send(method, path, **kwargs):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                response = client.request(method, path, timeout=remaining, **kwargs)
                response.raise_for_status()
                return response.json()

            session = send("POST", "/sessions", json={"user": args.label})
            headers = {"Authorization": "Bearer " + session["session_token"]}
            task = send(
                "POST", "/tasks", headers=headers, json={"request": args.request}
            )
            while True:
                state = send("GET", "/tasks/" + task["task_id"], headers=headers)
                if state["status"] in {"completed", "failed"}:
                    output = {
                        key: state[key]
                        for key in (
                            "status",
                            "answer",
                            "cited_doc_ids",
                            "observed_doc_ids",
                            "steps",
                            "error",
                        )
                    }
                    print(json.dumps(output, ensure_ascii=False))
                    return 0 if state["status"] == "completed" else 1
                sleep(min(args.interval, max(0, deadline - time.monotonic())))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, TimeoutError):
        print("Task request failed or timed out")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
