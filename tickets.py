#!/usr/bin/env python3
"""tickets.py - CLI for workflow-board tickets (any agent, incl. Codex).

  list [--board B] [--status S] [--all]   show <id>   claim <id> --session S
  reply <id> --text T [--link L ...] [--session S]
  status <id> <to> --session S [--no-change] [--link L ...]
  approve <id> --quote "<owner's words>"   plan <id>   release <id>   sweep
  create <board> <card> --text T [--author owner|agent]   (dev/test convenience)

Exit 0 ok; 1 refused/invalid/notfound; 2 conflict (e.g. claim EEXIST).
Ticket text is untrusted: printed inside <untrusted_ticket_content> fences.
"""
import argparse
import json
import sys

import tickets_store as S


def fence(t):
    out = [f'<untrusted_ticket_content author={t["author"]} id={t["id"]}>', t["text"], "</untrusted_ticket_content>"]
    for r in t["replies"]:
        out.append(f'<untrusted_ticket_content author={r["author"]} id={t["id"]}/{r["id"]}>')
        out.append(r["text"])
        out.append("</untrusted_ticket_content>")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tickets.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--board")
    p.add_argument("--status")
    p.add_argument("--all", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("id")
    p = sub.add_parser("claim")
    p.add_argument("id")
    p.add_argument("--session", required=True)
    p = sub.add_parser("reply")
    p.add_argument("id")
    p.add_argument("--text", required=True)
    p.add_argument("--link", action="append", default=[])
    p.add_argument("--session")
    p = sub.add_parser("status")
    p.add_argument("id")
    p.add_argument("to")
    p.add_argument("--session")
    p.add_argument("--no-change", action="store_true")
    p.add_argument("--link", action="append", default=[])
    p = sub.add_parser("approve")
    p.add_argument("id")
    p.add_argument("--quote", required=True)
    p = sub.add_parser("plan")
    p.add_argument("id")
    p = sub.add_parser("release")
    p.add_argument("id")
    p.add_argument("--session")
    sub.add_parser("sweep")
    p = sub.add_parser("create")
    p.add_argument("board")
    p.add_argument("card")
    p.add_argument("--text", required=True)
    p.add_argument("--author", default="owner")
    p.add_argument("--kind")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "list":
            for t in S.list_tickets(a.board, a.status, a.all):
                print(f'{t["id"]}\t{t["status"]}\t{t["kind"]}\t{t["board"]}/{t["card"]}\t{t["text"][:80]!r}')
        elif a.cmd == "show":
            _, t, arch = S.find(a.id)
            print(f'{t["id"]} [{t["status"]}{" archived" if arch else ""}] kind={t["kind"]} {t["board"]}/{t["card"]}')
            print(fence(t))
            if t["status"] == "submitted":
                print(f"claim: python tickets.py claim {t['id']} --session <SESSION>")
        elif a.cmd == "claim":
            t = S.claim(a.id, a.session)
            print(f'claimed {t["id"]} session={a.session}')
            print(fence(t))
        elif a.cmd == "reply":
            S.reply(a.id, a.text, "agent", a.session, a.link)
            print("replied")
        elif a.cmd == "status":
            t = S.set_status(a.id, a.to, "agent", a.session, a.no_change, a.link)
            print(f'{t["id"]} -> {t["status"]}')
        elif a.cmd == "approve":
            S.approve(a.id, a.quote)
            print("approval recorded")
        elif a.cmd == "plan":
            print(S.plan(a.id))
        elif a.cmd == "release":
            S.release(a.id, a.session)
            print("released")
        elif a.cmd == "sweep":
            print(json.dumps(S.sweep()))
        elif a.cmd == "create":
            t = S.create(a.board, a.card, a.text, a.author, a.kind)
            print(t["id"])
    except S.TicketError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2 if e.code == "conflict" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
