#!/usr/bin/env bash
# ── DTM AI → Hermes kanban delegation wrapper ────────────────────────────────────────────────────
# Lets the DTM AI web service (user `dtm-ai`) DELEGATE work by creating/assigning Hermes kanban
# tasks WITHOUT granting it docker access. The web service can't `docker exec`; this wrapper is the
# only bridge, and it is deliberately tiny and paranoid.
#
# SECURITY MODEL — why this is safe to expose via NOPASSWD sudo:
#   1. Installed ROOT-OWNED at /usr/local/sbin/dtm-ai-kanban.sh, mode 0755 — `dtm-ai` CANNOT modify
#      it (the repo copy is never what sudo runs; see deploy/hermes/install-kanban.sh).
#   2. Whitelists exactly two actions (create, assign). Anything else exits non-zero.
#   3. Every argument is validated (profile/tenant/id regexes, length caps). Unknown flags rejected.
#   4. NO shell passthrough: args are assembled into a `docker exec` argv ARRAY and exec'd directly —
#      never `bash -c`, never `eval`. So a task title can't inject a command.
#   5. It only ever touches the agent's OWN task board — it cannot reach client systems (those stay
#      behind the MCP fence + Capability Console). Delegating a task ≠ acting on a client.
# Invoked only via deploy/sudoers-dtm-ai-kanban.snippet. Stdout is the `--json` task object.
set -euo pipefail

DOCKER="${DOCKER_BIN:-/usr/bin/docker}"
CONTAINER="${HERMES_CONTAINER:-hermes}"
HBIN="${HERMES_BIN:-/opt/hermes/bin/hermes}"
HOME_ENV="HERMES_HOME=${HERMES_DATA:-/opt/data}"
NAME_RE='^[a-z0-9_-]+$'          # profile / tenant slug
ID_RE='^[A-Za-z0-9_:.-]+$'       # task id / idempotency key

die(){ echo "dtm-ai-kanban: $*" >&2; exit 2; }

action="${1:-}"; shift || true
case "$action" in
  create|assign|dispatch|archive) ;;
  *) die "action not allowed: '${action}'";;
esac

# Base argv — fixed, no user input.
dx=("$DOCKER" exec -u hermes -e "$HOME_ENV" "$CONTAINER" "$HBIN" kanban "$action")

if [[ "$action" == "create" ]]; then
  title=""; body=""; assignee=""; tenant=""; idem=""; createdby="dtm-ai"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --title)           title="${2:-}";     shift 2;;
      --body)            body="${2:-}";       shift 2;;
      --assignee)        assignee="${2:-}";   shift 2;;
      --tenant)          tenant="${2:-}";     shift 2;;
      --idempotency-key) idem="${2:-}";       shift 2;;
      --created-by)      createdby="${2:-}";  shift 2;;
      *) die "unknown create arg: '$1'";;
    esac
  done
  [[ -n "$title" ]]        || die "title required"
  [[ "$title" != -* ]]     || die "title cannot start with '-'"   # keep it a clean positional
  [[ ${#title} -le 200 ]]  || die "title too long (max 200)"
  [[ ${#body}  -le 8000 ]] || die "body too long (max 8000)"
  [[ ${#createdby} -le 80 ]] || die "created-by too long"
  # `hermes kanban create` takes the title as a POSITIONAL arg (not --title); flags follow.
  dx+=("$title" --json --created-by "$createdby")
  [[ -n "$body" ]] && dx+=(--body "$body")
  if [[ -n "$assignee" ]]; then
    [[ "$assignee" =~ $NAME_RE ]] || die "bad assignee"
    dx+=(--assignee "$assignee")
  fi
  if [[ -n "$tenant" ]]; then
    [[ "$tenant" =~ $NAME_RE || "$tenant" == "*" ]] || die "bad tenant"
    dx+=(--tenant "$tenant")
  fi
  if [[ -n "$idem" ]]; then
    [[ "$idem" =~ $ID_RE && ${#idem} -le 120 ]] || die "bad idempotency-key"
    dx+=(--idempotency-key "$idem")
  fi

elif [[ "$action" == "assign" ]]; then
  taskid="${1:-}"; profile="${2:-}"
  [[ "$taskid" =~ $ID_RE && ${#taskid} -le 120 ]] || die "bad task id"
  [[ "$profile" == "none" || "$profile" =~ $NAME_RE ]] || die "bad profile"
  dx+=("$taskid" "$profile")

elif [[ "$action" == "archive" ]]; then
  # Archive one or more task ids (clears finished cards). Each id validated; no flags accepted.
  [[ $# -ge 1 ]] || die "archive needs at least one task id"
  for tid in "$@"; do
    [[ "$tid" =~ $ID_RE && ${#tid} -le 120 ]] || die "bad task id: '$tid'"
    dx+=("$tid")
  done

elif [[ "$action" == "dispatch" ]]; then
  # One dispatcher pass (reclaim stale, promote ready, spawn workers). Idempotent — only spawns
  # ready+unclaimed tasks, so it's safe to call after every create. Optional --max cap (1..32).
  maxn=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --max) maxn="${2:-}"; shift 2;;
      *) die "unknown dispatch arg: '$1'";;
    esac
  done
  dx+=(--json)
  if [[ -n "$maxn" ]]; then
    [[ "$maxn" =~ ^[0-9]+$ && "$maxn" -ge 1 && "$maxn" -le 32 ]] || die "bad --max"
    dx+=(--max "$maxn")
  fi
fi

exec "${dx[@]}"
