"""{{AGENT_NAME}} - SOTA Agent"""
import asyncio
from sota_sdk import SOTAAgent, JobContext

agent = SOTAAgent()


@agent.on_job("echo")
async def handle_echo(ctx: JobContext):
    """Example handler: echoes back the job description."""
    await ctx.update_progress(50, "Processing...")
    return f"Echo: {ctx.job.description}"


if __name__ == "__main__":
    asyncio.run(agent.run())
