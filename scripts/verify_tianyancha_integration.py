"""Run a sanitized Tianyancha MCP integration check using environment credentials."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from jindiao.application.settings import Settings
from jindiao.tianyancha import (
    CapabilityRoutingConfig,
    StreamableHttpMcpTransport,
    TianyanchaMcpClient,
)
from jindiao.tianyancha.verification import verify_tianyancha_connection


async def _run(company_name: str, empty_query: str) -> None:
    settings = Settings()
    authorization = settings.tianyancha_authorization
    if authorization is None:
        raise SystemExit("TIANYANCHA_MCP_AUTHORIZATION is required")
    transport = StreamableHttpMcpTransport(
        settings.tianyancha_mcp_url,
        authorization,
        timeout_seconds=settings.request_timeout_seconds,
    )
    client = TianyanchaMcpClient(
        transport,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=1,
    )
    try:
        summary = await verify_tianyancha_connection(
            client=client,
            company_name=company_name,
            empty_query=empty_query,
            routing=CapabilityRoutingConfig.from_file(
                Path("config/tianyancha-capability-routes.json")
            ),
        )
        print(summary.model_dump_json(indent=2))
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify Tianyancha subject, capabilities, records, and empty-result semantics."
    )
    parser.add_argument("--company-name", default="华为技术有限公司")
    parser.add_argument("--empty-query", default="不存在企业JINDIAOEMPTY9F6E2C")
    args = parser.parse_args()
    asyncio.run(_run(args.company_name, args.empty_query))


if __name__ == "__main__":
    main()
