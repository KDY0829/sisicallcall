"""
진짜 MCP protocol Client — KDT-101.

기존 ``app/services/mcp/client.py`` (MCPClient) 는 Connector registry/router
역할로 보존하고, 진짜 MCP Client 는 이 패키지에 둔다. 이름이 겹치지 않도록
디렉터리명도 ``protocol_client`` 로 분리했다.

엔트리:
  from app.services.mcp.protocol_client.client import (
      MCPProtocolClient,
      get_default_protocol_client,
  )
"""

from app.services.mcp.protocol_client.client import (  # noqa: F401
    MCPProtocolClient,
    MCPClientTransportError,
    get_default_protocol_client,
)
