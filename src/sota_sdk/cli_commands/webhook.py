"""Webhook helpers: verify + test."""

import hashlib
import hmac
import json
import sys
import time

import click
import httpx


@click.group()
def webhook():
    """Webhook helpers."""


@webhook.command()
@click.argument("body_file", type=click.Path(exists=True, allow_dash=True))
@click.option("--sig", required=True,
              help="Signature from the X-SOTA-Signature header.")
@click.option("--secret", default=None, envvar="SOTA_WEBHOOK_SECRET",
              help="Webhook secret (default: SOTA_WEBHOOK_SECRET env).")
def verify(body_file, sig, secret):
    """Verify an HMAC-SHA256 signature against a raw body file.

    Closes the 'stringified body' footgun — always HMACs the raw bytes
    as delivered.
    """
    if not secret:
        click.echo(
            "Error: SOTA_WEBHOOK_SECRET not set and --secret not provided.",
            err=True,
        )
        raise SystemExit(1)
    if body_file == "-":
        body = sys.stdin.buffer.read()
    else:
        with open(body_file, "rb") as f:
            body = f.read()
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if hmac.compare_digest(computed, sig):
        click.echo("\u2713 Signature valid.")
        return
    click.echo(
        f"\u2717 Signature invalid. Expected {computed}, got {sig}.",
        err=True,
    )
    raise SystemExit(1)


@webhook.command("test")
@click.option("--url", required=True, help="Local handler URL.")
@click.option("--job-id", default="test-job-abc")
@click.option("--secret", default=None, envvar="SOTA_WEBHOOK_SECRET")
def test_cmd(url, job_id, secret):
    """Send a synthetic signed webhook to your local handler."""
    if not secret:
        click.echo("Error: SOTA_WEBHOOK_SECRET not set.", err=True)
        raise SystemExit(1)
    body = json.dumps({
        "event": "job.executing",
        "job_id": job_id,
        "ts": int(time.time()),
    }).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    r = httpx.post(url, content=body, headers={
        "Content-Type": "application/json",
        "X-SOTA-Signature": sig,
    })
    click.echo(f"POST {url} \u2192 {r.status_code}")
    if r.text:
        click.echo(r.text)
