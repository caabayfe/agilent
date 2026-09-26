"""Print the skill catalogue of the MCP server as one persona sees it.

    python -m cfa.skills.show bob        (or: make skills PERSONA=bob)

Uses the same path as the agent: user login -> token exchange -> MCP session with
the delegated token -> ``skills://catalog`` resource and ``tools/list``.
"""

# ruff: noqa: T201 - a CLI whose job is printing

import asyncio
import sys
import uuid

from cfa.agent.skills_client import SkillClient
from cfa.config import agent_settings, identity_settings
from cfa.identity.client import IdpClient

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


async def main(username: str) -> None:
    identity = identity_settings()
    idp = IdpClient(identity.idp_url, identity.agent_client_id, identity.agent_client_secret.get_secret_value())
    delegated = await idp.exchange_for_skills(await idp.login(username))
    catalog = await SkillClient(agent_settings().skill_server_url).catalog(delegated, uuid.uuid4().hex)
    skills = catalog["skills"]
    print(
        f"{BOLD}{catalog['server']}{RESET}  one MCP server, {len(skills)} skills, as seen by {BOLD}{username}{RESET}\n"
    )
    for skill in skills:
        access = skill["access"]
        badge = f"{GREEN}allowed{RESET}" if access["allowed"] else f"{RED}denied: {access['reason']}{RESET}"
        print(f"  {BOLD}{skill['name']:<8}{RESET} {skill['title']:<26} tier {skill['risk_tier']}   {badge}")
        scope, role = skill["required_agent_scope"], skill["required_user_role"]
        print(f"  {DIM}{'':<8} agent scope {scope:<13} user role {role}{RESET}")
        for tool in skill["tools"]:
            print(f"  {'':<8}   - {tool['name']}: {DIM}{tool['description'].splitlines()[0]}{RESET}")
        print()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "alice"))
