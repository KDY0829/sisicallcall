"""
KDT-101: MCP_EXECUTION_MODE 분기 테스트.

direct / mcp / mcp_with_fallback 모드에 따라 ActionExecutor 가
- direct mode: 기존 registry handler 만 호출
- mcp mode: MCPGatewayConnector 만 호출 (direct connector 호출 금지)
- mcp_with_fallback: gateway 가 transport error 일 때만 direct fallback

을 보장하는지 확인한다.

외부 API 는 호출하지 않는다 — gateway / handler 는 fake 로 주입한다.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _action(tool="slack", action_type="send_slack_alert", **extra) -> dict:
    base = {"tool": tool, "action_type": action_type, "params": {}}
    base.update(extra)
    return base


class FakeHandler:
    def __init__(self, *, status="success", external_id="ext-direct", result=None, error=None):
        self.status = status
        self.external_id = external_id
        self.result = result or {}
        self.error = error
        self.calls: list[dict] = []

    async def execute(self, action, *, call_id, tenant_id=""):
        self.calls.append({"action": action, "call_id": call_id, "tenant_id": tenant_id})
        return {
            "status": self.status,
            "external_id": self.external_id,
            "result": self.result,
            "error": self.error,
        }


class FakeGateway:
    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        raise_transport_err: bool = False,
        raise_other: bool = False,
    ):
        self.result = result or {
            "status": "success",
            "external_id": "ext-mcp",
            "result": {"source": "mcp_server", "via_mcp": True, "execution_mode": "mcp"},
        }
        self.raise_transport_err = raise_transport_err
        self.raise_other = raise_other
        self.calls: list[dict] = []

    async def execute(self, action, *, call_id, tenant_id):
        self.calls.append({"action": action, "call_id": call_id, "tenant_id": tenant_id})
        if self.raise_transport_err:
            from app.services.mcp.protocol_client import MCPClientTransportError
            raise MCPClientTransportError("simulated_transport_failure")
        if self.raise_other:
            raise RuntimeError("non_transport")
        return self.result


@pytest.fixture(autouse=True)
def _no_idempotency(monkeypatch):
    """idempotency lookup 이 항상 None 을 반환하도록 패치 — 다른 테스트
    상태가 누설되지 않게 격리."""
    async def _none(call_id, action_type, tool):
        return None
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.find_successful_action", _none,
    )


# ── direct mode ──────────────────────────────────────────────────────────────


def test_direct_mode_uses_registry_handler(monkeypatch):
    """MCP_EXECUTION_MODE=direct → registry handler.execute() 만 호출한다."""
    monkeypatch.setenv("MCP_EXECUTION_MODE", "direct")

    from app.agents.post_call.actions import executor as ex_mod
    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler(status="success", external_id="direct-id")
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway()
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-direct",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert len(fake_handler.calls) == 1
    assert len(fake_gateway.calls) == 0
    assert result[0]["status"] == "success"
    assert result[0]["external_id"] == "direct-id"


# ── mcp mode ─────────────────────────────────────────────────────────────────


def test_mcp_mode_uses_gateway_only(monkeypatch):
    """MCP_EXECUTION_MODE=mcp → gateway 만 호출, direct handler 호출 금지."""
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp")

    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler()
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway(
        result={
            "status": "success",
            "external_id": "C12345:1700000000.000100",
            "result": {
                "channel": "C12345",
                "ts": "1700000000.000100",
                "source": "mcp_server",
                "via_mcp": True,
                "execution_mode": "mcp",
                "mcp_tool": "slack.send_slack_alert",
            },
        }
    )
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-mcp",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert len(fake_gateway.calls) == 1
    assert len(fake_handler.calls) == 0, "direct handler MUST NOT be called in MCP mode"
    out = result[0]
    assert out["status"] == "success"
    assert out["external_id"] == "C12345:1700000000.000100"
    assert out["result"]["source"] == "mcp_server"
    assert out["result"]["via_mcp"] is True
    assert out["result"]["execution_mode"] == "mcp"


def test_mcp_mode_transport_error_does_not_fallback(monkeypatch):
    """MCP_EXECUTION_MODE=mcp → transport error 라도 direct fallback 금지."""
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp")

    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler()
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway(raise_transport_err=True)
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-mcp-tx",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert len(fake_gateway.calls) == 1
    assert len(fake_handler.calls) == 0, "direct handler MUST NOT be called in MCP mode"
    assert result[0]["status"] == "failed"
    assert "mcp_transport_failed" in result[0]["error"]


def test_mcp_mode_unknown_tool_returns_failed(monkeypatch):
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp")

    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler()
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway()
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-mcp-unknown",
        tenant_id="ten-1",
        actions=[_action(tool="weather", action_type="forecast")],
    ))

    assert result[0]["status"] == "failed"
    assert result[0]["error"].startswith("unknown_mcp_tool")
    assert len(fake_gateway.calls) == 0
    assert len(fake_handler.calls) == 0


# ── mcp_with_fallback ────────────────────────────────────────────────────────


def test_mcp_with_fallback_falls_back_only_on_transport_error(monkeypatch):
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp_with_fallback")

    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler(status="success", external_id="fallback-id")
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway(raise_transport_err=True)
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-fb",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert len(fake_gateway.calls) == 1
    assert len(fake_handler.calls) == 1, "direct fallback should run when transport fails"
    assert result[0]["status"] == "success"
    assert result[0]["external_id"] == "fallback-id"
    assert result[0]["result"]["source"] == "direct_fallback"
    assert result[0]["result"]["execution_mode"] == "mcp_with_fallback"


def test_mcp_with_fallback_does_not_fallback_on_tool_failure(monkeypatch):
    """Gateway 가 정상적으로 status=failed 를 돌려주면 direct fallback 하지 않는다."""
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp_with_fallback")

    from app.agents.post_call.actions.executor import ActionExecutor

    fake_handler = FakeHandler(status="success", external_id="fallback-id")
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway(
        result={
            "status": "failed",
            "external_id": None,
            "error": "slack_http_error:500",
            "result": {
                "source": "mcp_server",
                "via_mcp": True,
                "execution_mode": "mcp",
            },
        }
    )
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-fb-2",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert len(fake_gateway.calls) == 1
    assert len(fake_handler.calls) == 0, "tool-level failure must not trigger direct fallback"
    assert result[0]["status"] == "failed"
    assert result[0]["error"] == "slack_http_error:500"
    assert result[0]["result"]["source"] == "mcp_server"


# ── idempotency 보존 ─────────────────────────────────────────────────────────


def test_idempotency_skip_still_applied_in_mcp_mode(monkeypatch):
    """MCP mode 라도 이미 성공한 action 은 skipped already_succeeded 로 단축."""
    monkeypatch.setenv("MCP_EXECUTION_MODE", "mcp")

    from app.agents.post_call.actions.executor import ActionExecutor

    async def already_done(call_id, action_type, tool):
        return {"external_id": "prev-id", "status": "success"}
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.find_successful_action",
        already_done,
    )

    fake_handler = FakeHandler()
    monkeypatch.setattr(
        "app.agents.post_call.actions.executor.get_handler",
        lambda tool: fake_handler,
    )
    fake_gateway = FakeGateway()
    monkeypatch.setattr(
        "app.services.mcp.connectors.mcp_gateway_connector.get_default_gateway",
        lambda: fake_gateway,
    )

    executor = ActionExecutor()
    result = asyncio.run(executor.execute_actions(
        call_id="c-idem",
        tenant_id="ten-1",
        actions=[_action()],
    ))

    assert result[0]["status"] == "skipped"
    assert result[0]["error"] == "already_succeeded"
    assert len(fake_gateway.calls) == 0
    assert len(fake_handler.calls) == 0
