#!/usr/bin/env python3
"""Transport. Carries a message out of this session and brings the reply back.

This is the only way to reach the person or the reference provider from here. There is no
interactive stdin: nobody is watching this terminal.

It computes nothing and decides nothing. It does not know what a good question is, it does
not judge an answer, and it cannot build a reference distribution. It moves a payload and
waits.

  ask-person          ask the person one question; prints their answer
  request-reference   hand an INT-1 request to the provider; prints where the artifact landed

Both block until a reply arrives. Both can come back with an outcome that is not an answer --
the person may end the session, the provider may report that no usable reference could be
built, nobody may reply at all. Those are real results and they are printed as such; they
are not errors to retry around.

Usage:
  python3 harness_client.py ask-person \
      --stage select \
      --question "Have you ever been diagnosed with this?" \
      --why "Some models were built on people already diagnosed and others on a general
             population, and which one fits you changes which model I should use." \
      [--hard-blocker] [--option Yes --option No]

  python3 harness_client.py request-reference \
      --request runs/<RUN_ID>/interpret/request.json

Exit codes:
  0  a reply arrived (read it on stdout)
  2  the person ended the session
  3  no usable reference could be built, or none was supplied
  4  nobody replied within the time allowed
  5  the message was malformed and was not sent
"""
import argparse
import json
import os
import sys
import time
import uuid

COMMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "comms")
COMMS = os.path.abspath(COMMS)
OUTBOX = os.path.join(COMMS, "outbox")
INBOX = os.path.join(COMMS, "inbox")

POLL = 0.3


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _send(payload):
    os.makedirs(OUTBOX, exist_ok=True)
    os.makedirs(INBOX, exist_ok=True)
    mid = payload["message_id"]
    # Write to a temp name and rename into place. The harness polls this directory, and a
    # rename is atomic -- without it the harness can read a half-written message and reject
    # it as malformed.
    tmp = os.path.join(OUTBOX, "." + mid + ".tmp")
    final = os.path.join(OUTBOX, mid + ".json")
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, final)
    return final


def _await(message_id, timeout):
    path = os.path.join(INBOX, message_id + ".json")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            # The harness writes by rename too, so the file is complete when it appears.
            # The retry loop covers the case where it is not yet visible to this process.
            for _ in range(10):
                try:
                    with open(path) as fh:
                        return json.load(fh)
                except (json.JSONDecodeError, OSError):
                    time.sleep(0.05)
        time.sleep(POLL)
    return None


def cmd_ask_person(args):
    if not args.why or not args.why.strip():
        sys.stderr.write(
            "refusing to send: --why is required. The person is assumed to know nothing "
            "about genetics, and a question with no stated reason is what makes people "
            "guess at an answer.\n")
        return 5
    if not args.question.strip():
        sys.stderr.write("refusing to send: --question is empty.\n")
        return 5

    mid = "q-" + uuid.uuid4().hex[:12]
    _send({
        "message_id": mid,
        "kind": "ask-person",
        "sent_utc": _now(),
        "stage": args.stage,
        "question": args.question.strip(),
        "why_it_matters": args.why.strip(),
        "hard_blocker": bool(args.hard_blocker),
        "options": list(args.option or []),
    })

    reply = _await(mid, args.timeout)
    if reply is None:
        sys.stderr.write("no reply within %ds.\n" % args.timeout)
        print("[no reply from the person]")
        return 4

    status = reply.get("status")
    if status == "answer":
        # skipped means the person chose "I don't know" on a question that allowed it. The
        # text is printed either way; the marker is what says the uncertainty is real rather
        # than a guess, and it has to survive into the stage's recorded judgment.
        if reply.get("skipped"):
            print("[the person did not know] " + (reply.get("text") or "").strip())
        else:
            print((reply.get("text") or "").strip())
        return 0
    if status == "session_end":
        print("[the person ended the session] " + (reply.get("note") or "").strip())
        return 2
    sys.stderr.write("unexpected reply status %r\n" % status)
    print(json.dumps(reply))
    return 5


def cmd_request_reference(args):
    req = os.path.abspath(args.request)
    if not os.path.exists(req):
        sys.stderr.write("no request at %s\n" % req)
        return 5
    try:
        with open(req) as fh:
            json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        sys.stderr.write("request is not readable JSON: %s\n" % exc)
        return 5

    mid = "r-" + uuid.uuid4().hex[:12]
    _send({
        "message_id": mid,
        "kind": "request-reference",
        "sent_utc": _now(),
        "request_path": req,
    })

    reply = _await(mid, args.timeout)
    if reply is None:
        sys.stderr.write("no reply within %ds.\n" % args.timeout)
        print("[no reference was delivered]")
        return 4

    status = reply.get("status")
    if status == "delivered":
        # Print only the path. The caller passes it to reference_adapter.py, which is what
        # validates the artifact against the contract -- this tool must not pre-empt that
        # judgment by summarising or checking the contents.
        print(reply["artifact_path"])
        return 0
    if status == "unusable":
        print("[no usable reference could be built] " + (reply.get("reason") or ""))
        if reply.get("artifact_path"):
            # The provider said usable:false in a well-formed artifact. That is INT-2's
            # refusal and a correct outcome, so the artifact still goes to the adapter --
            # which will record it as such rather than as a missing reference.
            print(reply["artifact_path"])
        return 3
    if status == "unavailable":
        print("[no reference distribution was supplied] " + (reply.get("reason") or ""))
        return 3
    sys.stderr.write("unexpected reply status %r\n" % status)
    print(json.dumps(reply))
    return 5


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask-person", help="ask the person one question and wait")
    a.add_argument("--stage", required=True)
    a.add_argument("--question", required=True)
    a.add_argument("--why", required=True,
                   help="why this question matters, in plain language. Required.")
    a.add_argument("--hard-blocker", action="store_true",
                   help="set when you genuinely cannot proceed without an answer")
    a.add_argument("--option", action="append",
                   help="an offered answer; may be repeated. Free text is still accepted.")
    a.add_argument("--timeout", type=int, default=900)
    a.set_defaults(fn=cmd_ask_person)

    r = sub.add_parser("request-reference", help="hand an INT-1 request to the provider")
    r.add_argument("--request", required=True)
    r.add_argument("--timeout", type=int, default=1800)
    r.set_defaults(fn=cmd_request_reference)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
