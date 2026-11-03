"""Pay-per-event charging on Apify. Standard library only.

Used only when the checker runs as an Apify Actor (ACTOR_RUN_ID and APIFY_TOKEN are set by Apify inside the run).
API: POST https://api.apify.com/v2/actor-runs/{runId}/charge  body {"eventName", "count", "requestId"?}
     header idempotency-key (required). It must be called from the run itself with the run's own token.
     https://docs.apify.com/api/v2/post-charge-run

The token is read from the environment at call time and is never printed, logged or returned.
A failed charge does not change the answer the agent gets; the failure is written to stderr without the token.
"""
import json
import os
import sys
import urllib.request
import uuid

API = "https://api.apify.com/v2/actor-runs/{run_id}/charge"


def build_request(run_id, token, event, request_id=None):
    body = {"eventName": event, "count": 1}
    if request_id:
        body["requestId"] = request_id
    req = urllib.request.Request(
        API.format(run_id=run_id), data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token,
                 "idempotency-key": str(uuid.uuid4())})
    return req


def charge_from_env(opener=None):
    """Return charge(event, request_id) if running inside an Apify run, else None."""
    run_id = os.environ.get("ACTOR_RUN_ID")
    if not run_id or not os.environ.get("APIFY_TOKEN"):
        return None
    send = opener or (lambda req: urllib.request.urlopen(req, timeout=10))

    def charge(event, request_id=None):
        try:
            send(build_request(run_id, os.environ["APIFY_TOKEN"], event, request_id))
            return True
        except Exception as e:  # never break the answer; never print the token
            sys.stderr.write(f"charge failed for event {event}: {type(e).__name__}\n")
            return False

    return charge
