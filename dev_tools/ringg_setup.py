"""Set up a Ringg.AI agent from a spec file, so an agent can be rebuilt the same way for dev, production or a new workspace.

    python -m dev_tools.ringg_setup voices --language en-US     list voices (read-only; needs RINGG_API_KEY)
    python -m dev_tools.ringg_setup tools                        the tool checklist for the agent (offline)
    python -m dev_tools.ringg_setup plan   dev_tools/ringg_agents/b3-dev.json [--stores-dir stores]    what create would send (offline)
    python -m dev_tools.ringg_setup create dev_tools/ringg_agents/b3-dev.json [--stores-dir stores] [--yes]
    python -m dev_tools.ringg_setup show   <agent id>            a safe summary of an agent (read-only)

`create` is a dry run unless --yes is given. With --yes it creates ONE agent: POST /public/agent (a short placeholder prompt),
then PATCH /agent/v1 to set the full voice prompt, the website domains and the webhook subscription. The agent's tools are not
in the Ringg API (only in the dashboard and in Ringg's workspace MCP), so `tools` prints what to enter there.

Settings come from the environment, never from arguments, and are never printed:
    RINGG_API_KEY           the Ringg workspace key (voices, create, show)
    BOT_PUBLIC_URL          where Ringg reaches the bot, e.g. the ngrok address or the AWS address (webhook, tool addresses)
    RINGG_WEBHOOK_SECRET    the secret Ringg sends with call events; the same value the bot is started with
Requests go only to the Ringg API host below. Nothing here runs inside the bot service.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid as _uuid
from pathlib import Path
from typing import Any, Callable

BASE_URL = "https://prod-api.ringg.ai/ca/api/v0"
KEY_ENV = "RINGG_API_KEY"
RINGG_DOMAIN = "https://www.ringg.ai"            # Ringg requires this entry to stay in a web agent's allowed domains

AGENT_TYPES = ("inbound", "outbound", "outbound_inbound")
LANGUAGES = ("en-US", "en-GB", "en-AU", "en-IN", "hi-IN", "ta-IN", "te-IN", "bn-IN", "mr-IN", "kn-IN", "ms-MY", "zh-CN", "od-IN",
             "pa-IN", "my-MM")
EVENTS = ("call_started", "call_ringing", "call_ongoing", "call_completed", "chat_completed", "recording_completed",
          "platform_analysis_completed", "client_analysis_completed", "all_processing_completed")
METHODS = ("GET", "POST", "PUT", "PATCH")
_SPEC_KEYS = {"agent_name", "vertical", "agent_type", "primary_language", "voice_id", "intro_message", "custom_variables",
              "call_config", "whitelisted_domains", "event_subscriptions"}
_VAR = re.compile(r"[a-z][a-z0-9_]{0,40}")
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}")
_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_SECRETISH = re.compile(r"secret|key|token|authorization|password", re.I)

# The create call needs four prompt sections with length caps (2000, 3000, 1500, 5000). The full voice prompt is longer than
# one section allows, so create gets short placeholders and `edit_prompt` then replaces the whole prompt.
_PLACEHOLDER_INTRO = "You are the shopping assistant for {{store_name}}."
_PLACEHOLDER_GUIDELINES = "The full instructions are loaded in the next step."
_PLACEHOLDER_TASK = "Answer shoppers' questions using the store tools."


class SetupError(Exception):
    """A problem the person can fix; the message is safe to print."""


# ---------- spec ----------

def load_spec(path: str | Path) -> dict[str, Any]:
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SetupError(f"cannot read the spec file {path}: {exc}") from None
    return check_spec(spec)


def check_spec(spec: Any) -> dict[str, Any]:
    from bot.verticals import VERTICALS

    if not isinstance(spec, dict):
        raise SetupError("the spec must be a JSON object")
    unknown = sorted(set(spec) - _SPEC_KEYS)
    if unknown:
        raise SetupError(f"unknown field in the spec: {', '.join(unknown)}")
    name = spec.get("agent_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 100:
        raise SetupError("agent_name is required (up to 100 characters)")
    if spec.get("vertical") not in VERTICALS:
        raise SetupError(f"vertical must be one of: {', '.join(sorted(VERTICALS))}")
    if spec.get("agent_type", "inbound") not in AGENT_TYPES:
        raise SetupError(f"agent_type must be one of: {', '.join(AGENT_TYPES)}")
    if spec.get("primary_language") not in LANGUAGES:
        raise SetupError(f"primary_language must be one of: {', '.join(LANGUAGES)}")
    voice = spec.get("voice_id", "")
    if voice:
        try:
            _uuid.UUID(str(voice))
        except ValueError:
            raise SetupError("voice_id must be a voice id (a UUID) from the `voices` command") from None
    intro = spec.get("intro_message")
    if not isinstance(intro, str) or not intro.strip() or len(intro) > 500:
        raise SetupError("intro_message is required (up to 500 characters)")
    variables = spec.get("custom_variables", [])
    if not isinstance(variables, list) or not all(isinstance(v, str) and _VAR.fullmatch(v) for v in variables):
        raise SetupError("custom_variables must be a list of lowercase names such as store_uuid")
    for needed in ("store_uuid", "store_name"):
        if needed not in variables:
            raise SetupError(f"custom_variables must include {needed}: the shared prompt and the tools depend on it")
    if spec.get("agent_type", "inbound") != "inbound" and not {"callee_name", "mobile_number"} <= set(variables):
        raise SetupError("an outbound agent must keep callee_name and mobile_number in custom_variables")
    call_config = spec.get("call_config", {})
    if not isinstance(call_config, dict):
        raise SetupError("call_config must be an object")
    domains = spec.get("whitelisted_domains", [RINGG_DOMAIN])
    if not isinstance(domains, list) or RINGG_DOMAIN not in domains or not all(_is_origin(d) for d in domains):
        raise SetupError(f"whitelisted_domains must be https:// addresses and must keep {RINGG_DOMAIN}")
    subs = spec.get("event_subscriptions", [])
    if not isinstance(subs, list):
        raise SetupError("event_subscriptions must be a list")
    for sub in subs:
        events = sub.get("event_type") if isinstance(sub, dict) else None
        if (not isinstance(events, list) or not events or not set(events) <= set(EVENTS)
                or sub.get("method_type", "POST") not in METHODS
                or not isinstance(sub.get("callback_url"), str) or not sub["callback_url"].startswith(("https://", "${"))
                or not isinstance(sub.get("headers", {}), dict)):
            raise SetupError("each event subscription needs event_type (known events), an https callback_url and a method_type")
    return spec


def _is_origin(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://") and bool(_HOST.fullmatch(value[len("https://"):]))


def domains_from_stores(stores_dir: str | Path) -> list[str]:
    """https://<host> for every allowed domain of every enabled store settings file (the filename is the store id)."""
    found: list[str] = []
    for path in sorted(Path(stores_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("enabled", True) is not True:
            continue
        for host in data.get("allowed_domains") or []:
            origin = f"https://{str(host).strip().lower()}"
            if _is_origin(origin) and origin not in found:
                found.append(origin)
    return found


# ---------- what gets sent ----------

def expand_env(value: Any, env: dict[str, str], missing: list[str]) -> Any:
    """Fill ${NAME} from the environment, recording the names (never the values) of any that are not set."""
    if isinstance(value, str):
        def fill(m: re.Match) -> str:
            got = env.get(m.group(1), "")
            if not got:
                missing.append(m.group(1))
                return m.group(0)
            return got
        return _ENV_REF.sub(fill, value)
    if isinstance(value, list):
        return [expand_env(v, env, missing) for v in value]
    if isinstance(value, dict):
        return {k: expand_env(v, env, missing) for k, v in value.items()}
    return value


def build_prompt(spec: dict[str, Any]) -> str:
    from bot.ringg_prompt import ringg_prompt
    return ringg_prompt(channel="voice", vertical=spec["vertical"])


def build_requests(spec: dict[str, Any], env: dict[str, str], stores_dir: str | None = None) -> dict[str, Any]:
    """Everything `create` would send, in order, with the problems that stop it. The new agent's id is filled in later."""
    problems: list[str] = []
    missing: list[str] = []
    if not spec.get("voice_id"):
        problems.append("voice_id is empty: run `voices --language " + spec["primary_language"] + "` and put a voice id in the spec")
    domains = list(spec.get("whitelisted_domains", [RINGG_DOMAIN]))
    if stores_dir:
        for origin in domains_from_stores(stores_dir):
            if origin not in domains:
                domains.append(origin)
    subs = expand_env(spec.get("event_subscriptions", []), env, missing)
    create = {
        "agent_name": spec["agent_name"],
        "introduction_and_objective": _PLACEHOLDER_INTRO,
        "response_guidelines": _PLACEHOLDER_GUIDELINES,
        "task": _PLACEHOLDER_TASK,
        "primary_language": spec["primary_language"],
        "voice_id": spec.get("voice_id", ""),
        "intro_message": spec["intro_message"],
        "agent_type": spec.get("agent_type", "inbound"),
        "custom_variables": list(spec["custom_variables"]),
    }
    if spec.get("call_config"):
        create["call_config"] = dict(spec["call_config"])
    prompt = build_prompt(spec)
    steps = [("set the full voice prompt", {"operation": "edit_prompt", "agent_prompt": prompt}),
             ("set the website domains", {"operation": "edit_agent_whitelisted_domains", "whitelisted_domains": domains})]
    if subs:
        steps.append(("set the webhook subscription", {"operation": "edit_event_subscriptions", "event_subscriptions": subs}))
    if missing:
        problems.append("not set in the environment: " + ", ".join(sorted(set(missing))))
    for sub in subs:
        if not missing and not str(sub.get("callback_url", "")).startswith("https://"):
            problems.append("the webhook address must start with https:// (Ringg will not call a plain http address)")
    return {"create": create, "steps": steps, "problems": problems, "prompt": prompt}


def mask(value: Any, key: str = "") -> Any:
    """A copy that is safe to print: secret-looking values and every webhook header value are hidden."""
    if isinstance(value, dict):
        in_headers = key == "headers"
        return {k: ("***" if in_headers or (isinstance(v, str) and _SECRETISH.search(k)) else mask(v, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    return value


def plan_lines(built: dict[str, Any]) -> list[str]:
    prompt = built["prompt"]
    lines = [f"1. POST /public/agent   (create the agent)", json.dumps(mask(built["create"]), indent=2), ""]
    for n, (label, body) in enumerate(built["steps"], start=2):
        shown = dict(body)
        if "agent_prompt" in shown:
            shown["agent_prompt"] = (f"<the voice prompt: {len(prompt)} characters, starts: {prompt.splitlines()[0]!r}, "
                                     f"{{{{store_name}}}} appears {prompt.count('{{store_name}}')} time(s)>")
        lines += [f"{n}. PATCH /agent/v1   ({label})", json.dumps(mask({"agent_id": "<the new agent id>", **shown}), indent=2), ""]
    lines.append("Problems:" if built["problems"] else "No problems found.")
    lines += [f"  - {p}" for p in built["problems"]]
    return lines


# ---------- the tool checklist (the tools are not in the Ringg API) ----------

def tool_checklist(public_url: str = "") -> list[dict[str, Any]]:
    from bot.ringg import VOICE_TOOLS
    from bot.tool_schemas import VISITOR_TOOL_DECLARATIONS
    base = (public_url or "<BOT_PUBLIC_URL>").rstrip("/")
    declared = {d["name"]: d for d in VISITOR_TOOL_DECLARATIONS}
    out = []
    for name in VOICE_TOOLS:
        decl = declared[name]
        props = decl.get("parameters", {}).get("properties", {})
        required = set(decl.get("parameters", {}).get("required") or [])
        out.append({
            "name": name, "method": "POST", "url": f"{base}/ringg/tools/{name}", "description": decl.get("description", ""),
            "headers": [
                {"name": "X-Ringg-Tools-Key", "kind": "static", "value": "this agent's tools key (RINGG_TOOLS_KEY); never print or share it"},
                {"name": "X-Ringg-Agent-Id", "kind": "static", "value": "the id of this agent"},
                {"name": "Store-UUID", "kind": "variable", "value": "store_uuid"},
                {"name": "X-Ringg-Call-Id", "kind": "optional", "value": "the call id, if Ringg offers it"},
            ],
            "parameters": [{"name": k, "type": v.get("type"), "required": k in required, "kind": "dynamic",
                            "description": v.get("description", "")} for k, v in props.items()],
        })
    return out


def tool_lines(checklist: list[dict[str, Any]]) -> list[str]:
    lines = ["Enter each tool in the dashboard (Tools) or with Ringg's workspace MCP. The Ringg API has no tools endpoint.", "",
             "Headers on every tool:  X-Ringg-Tools-Key = static;  X-Ringg-Agent-Id = static (this agent's id);",
             "                        Store-UUID = a VARIABLE header whose value is store_uuid;  X-Ringg-Call-Id = optional.",
             "NEVER make the store id a Dynamic header or parameter: the voice model would choose it, and a caller can steer the model.", ""]
    for tool in checklist:
        params = ", ".join(f"{p['name']}{'*' if p['required'] else ''}:{p['type']}" for p in tool["parameters"]) or "(none)"
        lines += [f"{tool['name']}", f"  {tool['method']} {tool['url']}", f"  body parameters (dynamic; * = required): {params}"]
    return lines


# ---------- talking to Ringg ----------

Sender = Callable[[str, str, dict[str, str], "bytes | None"], "tuple[int, bytes]"]


def _urllib_send(method: str, url: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
    if not url.startswith(BASE_URL + "/"):
        raise SetupError("refusing to send a request anywhere but the Ringg API")
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Client:
    def __init__(self, key: str, send: Sender = _urllib_send):
        if not key:
            raise SetupError(f"{KEY_ENV} is not set. Set it in this window first; do not paste it into a chat or a file.")
        self._key, self._send = key, send

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, str] | None = None) -> tuple[int, Any]:
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"X-API-KEY": self._key, "Accept": "application/json", "Content-Type": "application/json"}
        try:
            status, raw = self._send(method, url, headers, json.dumps(body).encode() if body is not None else None)
        except SetupError:
            raise
        except Exception as exc:                                       # network trouble: never echo the key
            raise SetupError(f"could not reach Ringg: {str(exc).replace(self._key, '***')}") from None
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            data = {"message": "(the reply was not JSON)"}
        return status, data


def _fail(what: str, status: int, data: Any) -> SetupError:
    message = data.get("message") if isinstance(data, dict) else None
    code = data.get("code") if isinstance(data, dict) else None
    return SetupError(f"{what} failed: HTTP {status}" + (f" ({code})" if code else "") + (f": {message}" if message else ""))


# ---------- commands ----------

def cmd_voices(client: Client, language: str | None, out: Callable[[str], None]) -> None:
    status, data = client.request("GET", "/agent/voices", query={"language": language} if language else None)
    if status != 200:
        raise _fail("listing voices", status, data)
    for v in data.get("voices", []):
        out(f"{v.get('id')}  {v.get('name')}  {v.get('gender')}  {','.join(v.get('languages') or [])}")


def cmd_create(client: Client, spec: dict[str, Any], env: dict[str, str], stores_dir: str | None, yes: bool,
               out: Callable[[str], None], allow_duplicate: bool = False) -> str | None:
    built = build_requests(spec, env, stores_dir)
    for line in plan_lines(built):
        out(line)
    if built["problems"]:
        raise SetupError("fix the problems above first; nothing was sent")
    if not yes:
        out("Dry run: nothing was sent. Add --yes to create the agent.")
        return None
    status, data = client.request("GET", "/agent/all")
    if status != 200:
        raise _fail("listing agents", status, data)
    agents = (data.get("data") or {}).get("agents") or []
    if not allow_duplicate and any(a.get("agent_display_name") == spec["agent_name"] for a in agents):
        raise SetupError(f"an agent named {spec['agent_name']!r} already exists; nothing was created. "
                         f"Use `show <agent id>` to look at it, or rename it in the spec.")
    status, data = client.request("POST", "/public/agent", built["create"])
    agent_id = (data.get("data") or {}).get("id") if isinstance(data, dict) else None
    if status != 200 or not agent_id:
        raise _fail("creating the agent", status, data)
    out(f"Created the agent. Its id is {agent_id}")
    for label, body in built["steps"]:
        status, data = client.request("PATCH", "/agent/v1", {"agent_id": agent_id, **body})
        if status != 200:
            raise SetupError(f"the agent {agent_id} exists, but the step '{label}' failed: HTTP {status}"
                             + (f": {data.get('message')}" if isinstance(data, dict) and data.get("message") else "")
                             + ". Do not create it again; fix the step and run it by hand or ask for help.")
        out(f"Done: {label}")
    out("")
    out("Next steps (not done by this script):")
    out(f'  1. Add this agent to RINGG_AGENT_STORES on the bot:  "{agent_id}": "*"   (any store with an enabled settings file)')
    out("  2. Give it its own tools key in RINGG_TOOLS_KEY, and enter its tools: run `tools` for the checklist.")
    out("  3. Check it with `show` and one test call before relying on it.")
    return agent_id


def summarize_agent(data: dict[str, Any]) -> list[str]:
    """A summary of GET /agent/{id} that never prints secrets: header values and tool settings are left out."""
    agent = data.get("agents") or {}
    versions = agent.get("version_details") or {}
    ver = versions.get(agent.get("active_agent_version_id")) or (next(iter(versions.values())) if versions else {})
    cfg = ver.get("agent_config") or {}
    lines = [f"type: {agent.get('agent_type')}   language: {ver.get('language')}   voice: {(ver.get('voice') or {}).get('name')}",
             f"custom variables: {', '.join(cfg.get('custom_variables') or []) or '(none)'}",
             f"first message: {str(cfg.get('intro_message'))[:120]}"]
    for sec in ((cfg.get("agent_prompt") or {}).get("prompt_sections") or []):
        text = str(sec.get("section_content", ""))
        lines.append(f"prompt section {sec.get('section_title')!r}: {len(text)} characters, {{{{store_name}}}} x{text.count('{{store_name}}')}")
    lines.append(f"website domains: {', '.join(ver.get('whitelisted_domains') or []) or '(none)'}")
    for sub in ver.get("event_subscriptions") or []:
        lines.append(f"webhook: {','.join(sub.get('event_type') or [])} -> {sub.get('callback_url')} "
                     f"(header names: {', '.join((sub.get('headers') or {}).keys()) or 'none'})")
    tools = [t.get("tool_name") or t.get("name") for t in ver.get("on_call_tools") or [] if t.get("tool_name") or t.get("name")]
    lines.append(f"on-call tools ({len(tools)}): {', '.join(tools) or '(none)'}")
    return lines


def cmd_show(client: Client, agent_id: str, out: Callable[[str], None]) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]{8,64}", agent_id):
        raise SetupError("that does not look like an agent id")
    status, data = client.request("GET", f"/agent/{agent_id}")
    if status != 200:
        raise _fail("reading the agent", status, data)
    for line in summarize_agent(data):
        out(line)


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, out: Callable[[str], None] = print,
         send: Sender = _urllib_send) -> int:
    env = dict(os.environ) if env is None else env
    ap = argparse.ArgumentParser(prog="ringg_setup", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("voices"); p.add_argument("--language")
    sub.add_parser("tools")
    for name in ("plan", "create"):
        p = sub.add_parser(name); p.add_argument("spec"); p.add_argument("--stores-dir")
        if name == "create":
            p.add_argument("--yes", action="store_true"); p.add_argument("--allow-duplicate", action="store_true")
    p = sub.add_parser("show"); p.add_argument("agent_id")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "tools":
            for line in tool_lines(tool_checklist(env.get("BOT_PUBLIC_URL", ""))):
                out(line)
        elif args.cmd == "plan":
            for line in plan_lines(build_requests(load_spec(args.spec), env, args.stores_dir)):
                out(line)
        elif args.cmd == "voices":
            cmd_voices(Client(env.get(KEY_ENV, ""), send), args.language, out)
        elif args.cmd == "show":
            cmd_show(Client(env.get(KEY_ENV, ""), send), args.agent_id, out)
        elif args.cmd == "create":
            cmd_create(Client(env.get(KEY_ENV, ""), send) if args.yes else Client("dry-run", send), load_spec(args.spec), env,
                       args.stores_dir, args.yes, out, args.allow_duplicate)
    except SetupError as exc:
        out(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
