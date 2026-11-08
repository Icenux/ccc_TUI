"""The Claude Code subagents part of ccc's HTTP API (see ccc.api; the TUI's Agents window, A, does the
same with ccc.agents). Not to be mixed up with ccc.api_agents, which is about reviews, goals and workflows.

GET    /api/v1/agents?path=                    the agents of a folder (its project's and the user's), without prompts
GET    /api/v1/agents/{scope}/{name}?path=     one agent with its prompt and a `version` (scope: project | user)
POST   /api/v1/agents                          create {"scope", "path", "name", "description", "tools", "model", "prompt"}
POST   /api/v1/agents/{scope}/{name}           change {"path", "description", "tools", "model", "prompt", "version"}
DELETE /api/v1/agents/{scope}/{name}?path=     remove (the file moves to ccc's own folder)
GET    /api/v1/agent-options                   templates and the wizard's choices, in the request's language
POST   /api/v1/agents/prompt                   {"description", "access", "claude"}: the instructions for a new agent,
                                               an outline or (claude: true) written by Claude

Everything that writes needs an admin device: an agent with Bash or Edit steers what Claude does in a project
(api_roles closes every path it does not list). `tools` is a list of tool names, null = every tool; `model` is
"" (the session's), sonnet, opus, haiku, fable or inherit. An agent's file is only ever found through the folder
lists (never built from a name in the request), and what is changed or removed must lie inside the folder asked
for — a project's agents of a parent folder are shown but not touched from here."""
from __future__ import annotations

import asyncio
import os
import threading
from http import HTTPStatus

from ccc import agent_templates, agent_wizard, agents, api_scope
from ccc.api_common import ApiError, route, text_of
from ccc.i18n import _

MAX_PROMPT = 20000
_generating = threading.Lock()  # one prompt written by Claude at a time: it costs usage


def version_of(agent: agents.Agent) -> str:
    return f"{agent.stamp[0]}-{agent.stamp[1]}"


def _bad(message: str) -> ApiError:
    return ApiError(HTTPStatus.BAD_REQUEST, message)


def _tools_of(body: dict) -> tuple[str, ...] | None:
    value = body.get("tools")
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise _bad(_("tools must be a list of tool names, or null for every tool."))
    return tuple(t.strip() for t in value if t.strip()) or ()


def _model_of(body: dict) -> str:
    value = body.get("model", "")
    if not isinstance(value, str):
        raise _bad(_("model must be text."))
    return value


def _prompt_of(body: dict) -> str:
    prompt = text_of(body, "prompt")
    if not prompt.strip():
        raise _bad(_("The agent needs instructions."))
    if len(prompt) > MAX_PROMPT:
        raise _bad(_("The instructions are too long."))
    return prompt


def _scope_of(value) -> str:
    if value not in (agents.SCOPE_PROJECT, agents.SCOPE_USER):
        raise _bad(_("scope must be project or user."))
    return value


class SubagentEndpoints:
    storage: object

    def _subagent_routes(self) -> list:
        ok = route
        return [
            ok(r"/agents", lambda ids, q, b: self.agents(q.get("path", ""))),
            ok(r"/agents", lambda ids, q, b: self.create_agent(b), "POST", HTTPStatus.CREATED),
            ok(r"/agents/prompt", lambda ids, q, b: self.agent_prompt(b), "POST"),
            ok(r"/agents/(project|user)/([^/]+)", lambda ids, q, b: self.agent(*ids, q.get("path", ""))),
            ok(r"/agents/(project|user)/([^/]+)", lambda ids, q, b: self.update_agent(*ids, b), "POST"),
            ok(r"/agents/(project|user)/([^/]+)", lambda ids, q, b: self.delete_agent(*ids, q.get("path", "")),
               "DELETE"),
            ok(r"/agent-options", lambda ids, q, b: self.agent_options()),
        ]

    # --- reading ---------------------------------------------------------------------

    def _folder(self, path: str, required: bool = False) -> str:
        if not path.strip():
            if required:
                raise _bad(_("Choose a directory."))
            return ""
        return api_scope.check_folder(self.storage, path)

    def agents(self, path: str) -> list[dict]:
        """The Claude Code subagents of a folder (its project's and the user's),
        without their prompts; a broken file comes with `problem`."""
        return [agents.as_dict(a) for a in agents.list_agents(self._folder(path))]

    def _find(self, scope: str, name: str, path: str) -> tuple[agents.Agent, str]:
        """The listed agent `name` of `scope` for a folder (the one Claude Code would use first) and the folder."""
        name = str(name)  # the route turns an all-digit name into a number
        folder = self._folder(path, required=scope == agents.SCOPE_PROJECT)
        found = [a for a in agents.list_agents(folder if scope == agents.SCOPE_PROJECT else "")
                 if a.scope == scope and a.name == name]
        if not found:
            raise ApiError(HTTPStatus.NOT_FOUND, _("There is no agent “{name}” here.").format(name=name))
        return next((a for a in found if not a.overridden), found[0]), folder

    @staticmethod
    def _inside(agent: agents.Agent, folder: str) -> None:
        """What is changed lies in the folder asked for: not a parent folder's agent, and not a file behind a
        symbolic link that leads out of it. (A user's agent is the user's own folder.)"""
        if agent.scope == agents.SCOPE_USER:
            return
        real_folder = os.path.realpath(folder)
        real_file = os.path.realpath(agent.path.parent)
        if real_file != real_folder and not real_file.startswith(real_folder + os.sep):
            raise ApiError(HTTPStatus.FORBIDDEN, _("This agent is outside the folder: change it on the computer."))

    def agent(self, scope: str, name: str, path: str) -> dict:
        found, _folder = self._find(scope, name, path)
        return {**agents.as_dict(found), "prompt": found.prompt, "version": version_of(found)}

    def agent_options(self) -> dict:
        """What the app needs to offer a new agent: the templates (texts in the request's language) and the
        wizard's two questions with what they mean."""
        templates = []
        for t in agent_templates.TEMPLATES:
            description, tools, model, prompt = t.parts()
            templates.append({"key": t.key, "title": _(t.title), "description": description, "tools": list(tools),
                              "model": model, "prompt": prompt})
        return {
            "templates": templates,
            "access": [{"key": c.key, "label": _(c.label), "tools": list(c.tools)} for c in agent_wizard.ACCESS],
            "effort": [{"key": c.key, "label": _(c.label), "model": c.model} for c in agent_wizard.EFFORT],
            "models": list(agents.MODELS),
            "default_access": agent_wizard.DEFAULT_ACCESS,
            "default_effort": agent_wizard.DEFAULT_EFFORT,
        }

    # --- writing ---------------------------------------------------------------------

    def create_agent(self, body: dict) -> dict:
        scope = _scope_of(body.get("scope"))
        folder = self._folder(text_of(body, "path"), required=scope == agents.SCOPE_PROJECT)
        try:
            path, new_folder = agents.write_agent(
                text_of(body, "name"), text_of(body, "description"), _tools_of(body), _model_of(body),
                _prompt_of(body), scope, folder)
        except ValueError as problem:
            raise _bad(str(problem)) from problem
        created = agents.read_file(path, scope)
        return {**(agents.as_dict(created) if created else {"name": text_of(body, "name")}),
                "new_folder": new_folder, "version": version_of(created) if created else ""}

    def update_agent(self, scope: str, name: str, body: dict) -> dict:
        found, folder = self._find(scope, name, text_of(body, "path"))
        self._inside(found, folder)
        if agents.valid_name(found.name):
            raise _bad(_("Change the name in an editor first: {problem}").format(problem=_(agents.valid_name(found.name))))
        version = body.get("version")
        if not isinstance(version, str) or version != version_of(found):
            raise ApiError(HTTPStatus.CONFLICT, _("The file changed since you opened it; open it again."))
        try:
            agents.update_agent(found, text_of(body, "description"), _tools_of(body), _model_of(body), _prompt_of(body))
        except ValueError as problem:
            raise _bad(str(problem)) from problem
        changed = agents.read_file(found.path, scope)
        return {**(agents.as_dict(changed) if changed else agents.as_dict(found)),
                "version": version_of(changed) if changed else ""}

    def delete_agent(self, scope: str, name: str, path: str) -> dict:
        found, folder = self._find(scope, name, path)
        self._inside(found, folder)
        try:
            agents.delete_agent(found)
        except ValueError as problem:
            raise _bad(str(problem)) from problem
        return {"removed": found.name}

    def agent_prompt(self, body: dict) -> dict:
        """The instructions of a new agent. An outline costs nothing; with "claude": true Claude writes them
        (one call without tools, as in the TUI), one at a time."""
        job = text_of(body, "description")
        access = body.get("access", agent_wizard.DEFAULT_ACCESS)
        if access not in agent_wizard.BY_ACCESS:
            raise _bad(_("Unknown access: {access}").format(access=access))
        if body.get("claude") is not True:
            return {"prompt": agent_wizard.outline_prompt(job, access), "by": "outline"}
        if not job.strip():
            raise _bad(_("Describe what the agent is for first."))
        if not _generating.acquire(blocking=False):
            raise ApiError(HTTPStatus.TOO_MANY_REQUESTS, _("Claude is already writing instructions; wait for it."))
        try:
            text = asyncio.run(agent_wizard.generate_prompt(job, agent_wizard.BY_ACCESS[access].tools))
        except ValueError as problem:
            raise ApiError(HTTPStatus.BAD_GATEWAY, str(problem)) from problem
        finally:
            _generating.release()
        return {"prompt": text, "by": "claude"}
