"""Safe, typed MCP tool failures. Never serialize raw exception text or inputs."""
import functools
import json

try:
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    from mcp.server.fastmcp.exceptions import ToolError


class SafeToolError(ToolError, ValueError):
    """ToolError keeps anticipated failures visible under both SDK generations."""
    def __init__(self, code, message, field=None, allowed=None):
        payload = {"code": code, "message": message}
        if field is not None:
            payload["field"] = field
        if allowed is not None:
            payload["allowed"] = allowed
        super().__init__(json.dumps({"ok": False, "error": payload}, ensure_ascii=False))


# Only reviewed, constant messages may reach clients. Raw browser/OS/validator
# errors can contain page text, credential-bearing URLs, paths or input values.
_RULES = [
    ("invalid task state", "invalid_state", "invalid task state", "state"),
    ("unknown or ambiguous task id", "not_found", "unknown or ambiguous task id", "task_id"),
    ("unknown session", "not_found", "unknown session id", "session_id"),
    ("task id required", "bad_argument", "task id required", "task_id"),
    ("blocked task needs", "bad_argument", "blocked task needs a clear blocker/question", "blocker"),
    ("done task needs", "bad_argument", "done task needs a result", "result"),
    ("acknowledge the command result", "precondition_failed", "acknowledge the command result before purging output", None),
    ("cannot purge active output", "precondition_failed", "cannot purge active output", "session_id"),
    ("task still has an active command", "precondition_failed", "task still has an active command; finish or cancel it first", "task_id"),
    ("exists (create_only)", "precondition_failed", "exists (create_only)", "path"),
    ("stale file", "precondition_failed", "stale file (sha mismatch or missing), re-read first", "expected_sha256"),
    ("match found", "precondition_failed", "match must occur exactly once", "match_text"),
    ("path outside allowed roots", "permission_denied", "path outside allowed roots; use HOME, TMPDIR or shared storage", "path"),
    ("limit must", "bad_argument", "limit must be >= 0", "limit"),
    ("stream must", "bad_argument", "stream must be stdout|stderr", "stream"),
    ("offset must", "bad_argument", "offset must be >= 0", "offset"),
    ("length must", "bad_argument", "length must be > 0", "length"),
    ("stream stdout|stderr", "bad_argument", "stream stdout|stderr, offset >= 0 and length > 0 required", None),
    ("bad mode", "bad_argument", "unsupported edit mode", "mode"),
    ("minimum interval", "bad_argument", "minimum interval 60s", "interval_seconds"),
    ("empty id", "bad_argument", "nonempty id required", None),
    ("task needs a title", "bad_argument", "task needs a title up to 500 chars and detail up to 20000 chars", "title"),
    ("task text too long", "bad_argument", "task text too long", None),
    ("command must be", "bad_argument", "command must be nonempty and at most 64000 chars", "command"),
    ("timeout must be", "bad_argument", "timeout must be within the supported range", "timeout"),
    ("idempotency key too long", "bad_argument", "idempotency key too long", "idempotency_key"),
    ("idempotency key was used", "precondition_failed", "idempotency key was used for a different request", "idempotency_key"),
    ("cwd must exist", "not_found", "cwd must exist and be a directory", "cwd"),
    ("artifact must be", "not_found", "artifact must be an existing file", "path"),
    ("saved output budget", "resource_limit", "saved output budget reached; purge reviewed output first", None),
    ("command concurrency", "resource_limit", "command concurrency limit reached; wait or cancel an active command", None),
    ("reopen terminal task", "precondition_failed", "reopen terminal task before starting more work", "task_id"),
    ("work data directory is already owned", "precondition_failed", "work data directory is already owned by another server", None),
    ("cannot import invalid state.json", "precondition_failed", "cannot import invalid state.json; restore it before starting", None),
    ("browser is closed", "precondition_failed", "browser is closed; call browser_open first", None),
    ("browser already open", "precondition_failed", "browser already open; navigate or close it first", None),
    ("stale/unknown ref", "not_found", "stale/unknown ref; call browser_snapshot again", "ref"),
    ("target changed", "precondition_failed", "target changed; take a fresh snapshot", "ref"),
    ("only HTTP/HTTPS", "bad_argument", "only HTTP/HTTPS URLs without embedded credentials are allowed", "url"),
    ("unsupported key", "bad_argument", "unsupported key", "key"),
    ("text too long", "bad_argument", "text too long", "text"),
    ("target must be input", "bad_argument", "target must be input/textarea", "ref"),
    ("human control active", "precondition_failed", "human control active; wait for the person to release control", None),
    ("secret field", "permission_denied", "secret field: use private live control, not tool text", "ref"),
    ("vault privacy mode", "permission_denied", "vault privacy mode: page output blocked; close browser to reset", None),
    ("vault target origin mismatch", "permission_denied", "vault target origin mismatch", None),
    ("vault origin mismatch", "permission_denied", "vault origin mismatch; fill refused", None),
    ("vault fill requires", "permission_denied", "vault fill requires a visible ordinary input", "ref"),
    ("vault fill failed", "precondition_failed", "vault fill failed; page output remains blocked to protect credentials", None),
    ("vault locked", "precondition_failed", "vault locked or unavailable; human must unlock with dot-mcp vault serve", None),
    ("vault auto-locked", "precondition_failed", "vault auto-locked; human unlock required", None),
    ("entry or field unavailable", "not_found", "entry or field unavailable", None),
]


def safe_failure(exc):
    if isinstance(exc, SafeToolError):
        return exc
    # SDKs wrap anticipated tool exceptions; recover only our typed safe cause.
    cause = exc.__cause__
    if isinstance(cause, SafeToolError):
        return cause
    if isinstance(exc, FileNotFoundError):
        return SafeToolError("not_found", "file or directory not found", "path")
    if isinstance(exc, PermissionError):
        return SafeToolError("permission_denied", "operation denied by filesystem permissions", "path")
    if isinstance(exc, (NotADirectoryError, IsADirectoryError)):
        return SafeToolError("bad_argument", "expected a different file or directory type", "path")
    # Inspect for classification only. No exception, input or path is echoed.
    if isinstance(exc, (ValueError, RuntimeError, ToolError)):
        text = str(exc)
        for prefix, code, message, field in _RULES:
            if text.startswith(prefix):
                allowed = None
                if code == "invalid_state":
                    from dot_mcp.work import TASK_STATES
                    allowed = sorted(TASK_STATES)
                return SafeToolError(code, message, field, allowed)
    return SafeToolError("tool_failed", "operation failed; check local server diagnostics")


def safe_tool(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            raise safe_failure(exc) from None
    return wrapped
