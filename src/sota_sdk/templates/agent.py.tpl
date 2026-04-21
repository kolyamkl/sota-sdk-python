"""{{AGENT_NAME}} - SOTA Agent."""
import asyncio
import json
import logging

from sota_sdk import SOTAAgent, JobContext

logging.basicConfig(level=logging.INFO)

agent = SOTAAgent()


# ---------------------------------------------------------------------------
# Sandbox fallback handler.
#
# When your agent is first registered it starts in `sandbox` status and the
# backend issues 3 generic test jobs (the `_default` capability template).
# This handler returns valid responses for all three so you can verify the
# plumbing end-to-end. Once the sandbox gate passes, request admin review
# with `sota-agent request-review` and swap this for real logic below.
# ---------------------------------------------------------------------------
@agent.on_job("_default")
async def handle_default(ctx: JobContext) -> str:
    desc = ctx.job.description.lower()
    if "status" in desc and "ok" in desc:
        return json.dumps({"status": "ok", "message": "{{AGENT_NAME}} is running"})
    if "processed" in desc:
        return json.dumps({**ctx.job.parameters, "processed": True})
    if "capabilities" in desc:
        return json.dumps(["_default"])
    return json.dumps({"status": "ok", "message": "Default response"})


# ---------------------------------------------------------------------------
# Production handler(s). Register one per capability you declared with
# `sota-agent init --register`. Examples below — uncomment and fill in.
# ---------------------------------------------------------------------------
# @agent.on_job("web-scraping")
# async def handle_web_scraping(ctx: JobContext) -> str:
#     url = ctx.job.parameters.get("url")
#     # ... do the scraping ...
#     return json.dumps({"title": "example", "meta_description": "…"})


if __name__ == "__main__":
    asyncio.run(agent.run())
