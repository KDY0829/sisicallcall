from __future__ import annotations

from app.agents.post_call.actions.registry import get_handler
from app.agents.post_call.actions.result import action_failed, action_skipped, action_success
from app.repositories.mcp_action_log_repo import find_successful_action
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ActionExecutor:
    """action_plan.actions 를 registry 의 handler 또는 MCP Gateway 로 라우팅하고
    표준 6-key 결과 list 를 반환한다.

    실행 모드는 ``MCP_EXECUTION_MODE`` 환경변수가 결정한다:

      direct (기본)
        registry handler.execute() 를 그대로 호출 — 기존 direct connector 경로.

      mcp
        MCPGatewayConnector.execute() 만 사용. direct fallback 금지.
        MCP server tool 이 status=failed 를 돌려줘도 그대로 failed 로 반환.

      mcp_with_fallback
        MCPGatewayConnector.execute() 를 먼저 시도하되, transport / server
        process 자체가 죽었을 때 (MCPClientTransportError) 만 direct
        handler 로 fallback. 외부 API failure 는 fallback 하지 않는다.

    새 tool 추가 시 executor.py 는 수정하지 않는다 — registry.py + MCP
    gateway tool name map 만 갱신하면 된다.
    """

    async def execute_actions(
        self,
        call_id: str,
        tenant_id: str,
        actions: list[dict] | None,
    ) -> list[dict]:
        if not actions:
            return []
        results: list[dict] = []
        for action in actions:
            results.append(
                await self._execute_one(action, call_id=call_id, tenant_id=tenant_id)
            )
        return results

    async def execute_all(self, actions: list[dict], *, call_id: str) -> list[dict]:
        """후방 호환 인터페이스 — action_router_node 가 호출한다."""
        return await self.execute_actions(
            call_id=call_id,
            tenant_id="",
            actions=actions,
        )

    async def _execute_one(
        self,
        action: dict,
        *,
        call_id: str,
        tenant_id: str = "",
    ) -> dict:
        from app.services.mcp.connectors.mcp_gateway_connector import (
            MCPClientTransportError,
            execution_mode,
            get_default_gateway,
            resolve_mcp_tool_name,
        )

        tool_key = action.get("tool", "")
        action_type = action.get("action_type", "")
        mode = execution_mode()

        # ── MCP mode 에서는 unknown tool 도 gateway 가 처리하므로 handler lookup
        # 은 direct 모드 또는 fallback 시점으로 미룬다. direct mode 만 미리 lookup.
        handler = None
        if mode == "direct":
            handler = get_handler(tool_key)
            if handler is None:
                logger.warning(
                    "알 수 없는 tool call_id=%s tool=%r action_type=%s",
                    call_id, tool_key, action_type,
                )
                return action_failed(action, error=f"unknown tool: {tool_key!r}")

        # ── idempotency check (모드 무관) ─────────────────────────────────────
        previous = await find_successful_action(
            call_id=call_id,
            action_type=action_type,
            tool=tool_key,
        )
        if previous:
            logger.info(
                "action idempotency skip call_id=%s tool=%s action_type=%s previous_external_id=%s",
                call_id,
                tool_key,
                action_type,
                previous.get("external_id"),
            )
            return action_skipped(
                action,
                reason="already_succeeded",
                result={
                    "idempotency": "already_succeeded",
                    "previous_external_id": previous.get("external_id"),
                    "previous_status": previous.get("status"),
                },
            )

        # ── 실행 모드 분기 ───────────────────────────────────────────────────
        if mode == "direct":
            return await self._run_direct(
                action, handler=handler, call_id=call_id, tenant_id=tenant_id,
            )

        # mcp / mcp_with_fallback
        if resolve_mcp_tool_name(tool_key, action_type) is None and mode == "mcp":
            logger.warning(
                "MCP mode unknown mapping call_id=%s tool=%s action_type=%s",
                call_id, tool_key, action_type,
            )
            return action_failed(
                action,
                error=f"unknown_mcp_tool:{tool_key}.{action_type}",
                result={
                    "source": "mcp_server",
                    "via_mcp": True,
                    "execution_mode": "mcp",
                },
            )

        gateway = get_default_gateway()
        try:
            raw = await gateway.execute(action, call_id=call_id, tenant_id=tenant_id)
            return self._raw_to_action_result(action, raw)
        except MCPClientTransportError as exc:
            logger.error(
                "MCP transport 오류 call_id=%s tool=%s action_type=%s err=%s mode=%s",
                call_id, tool_key, action_type, exc, mode,
            )
            if mode == "mcp_with_fallback":
                fallback_handler = get_handler(tool_key)
                if fallback_handler is None:
                    return action_failed(
                        action,
                        error=f"mcp_transport_failed_and_unknown_tool:{tool_key}",
                        result={
                            "source": "mcp_with_fallback",
                            "via_mcp": False,
                            "execution_mode": "mcp_with_fallback",
                            "transport_error": str(exc),
                        },
                    )
                logger.warning(
                    "mcp_with_fallback → direct fallback call_id=%s tool=%s",
                    call_id, tool_key,
                )
                fb = await self._run_direct(
                    action,
                    handler=fallback_handler,
                    call_id=call_id,
                    tenant_id=tenant_id,
                )
                # fallback 사용 사실을 result 에 기록.
                if isinstance(fb.get("result"), dict):
                    fb["result"].setdefault("execution_mode", "mcp_with_fallback")
                    fb["result"].setdefault("via_mcp", False)
                    fb["result"].setdefault("source", "direct_fallback")
                    fb["result"].setdefault("mcp_transport_error", str(exc))
                return fb

            # mcp mode — direct fallback 금지.
            return action_failed(
                action,
                error=f"mcp_transport_failed:{exc}",
                result={
                    "source": "mcp_server",
                    "via_mcp": True,
                    "execution_mode": "mcp",
                    "transport_error": str(exc),
                },
            )
        except Exception as exc:
            logger.error(
                "MCP gateway 예외 call_id=%s tool=%s action_type=%s err=%s",
                call_id, tool_key, action_type, exc,
            )
            return action_failed(action, error=str(exc))

    # ── direct handler 호출 ──────────────────────────────────────────────────

    @staticmethod
    def _raw_to_action_result(action: dict, raw: dict) -> dict:
        status = raw.get("status", "success")
        if status == "failed":
            return action_failed(
                action,
                error=raw.get("error") or "handler returned failed",
                result=raw.get("result"),
            )
        if status == "skipped":
            return action_skipped(
                action,
                reason=raw.get("error") or "handler returned skipped",
                result=raw.get("result"),
            )
        return action_success(
            action,
            external_id=raw.get("external_id"),
            result=raw.get("result"),
        )

    async def _run_direct(
        self,
        action: dict,
        *,
        handler,
        call_id: str,
        tenant_id: str,
    ) -> dict:
        try:
            raw: dict = await handler.execute(
                action,
                call_id=call_id,
                tenant_id=tenant_id,
            )
            return self._raw_to_action_result(action, raw)
        except Exception as exc:
            logger.error(
                "action 실패 call_id=%s tool=%s action_type=%s err=%s",
                call_id,
                action.get("tool", ""),
                action.get("action_type", ""),
                exc,
            )
            return action_failed(action, error=str(exc))


# ── 모듈 레벨 편의 함수 ───────────────────────────────────────────────────────

_default_executor = ActionExecutor()


async def execute_actions(
    call_id: str,
    tenant_id: str,
    actions: list[dict] | None,
) -> list[dict]:
    """모듈 레벨 편의 함수 — ActionExecutor().execute_actions() 와 동일."""
    return await _default_executor.execute_actions(
        call_id=call_id,
        tenant_id=tenant_id,
        actions=actions,
    )
