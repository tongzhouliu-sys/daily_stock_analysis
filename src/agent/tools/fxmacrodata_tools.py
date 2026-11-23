"""Public macroeconomic tools consumed by the stock analysis agent."""

from copy import deepcopy
import logging
import os

from fxmacrodata_public import FXMacroDataClient, list_operations
from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

logger = logging.getLogger(__name__)


class FXMacroDataTool(ToolDefinition):
    """Keep nested public JSON Schemas intact in every DSA tool descriptor."""

    input_schema: dict

    def _params_json_schema(self) -> dict:
        return deepcopy(self.input_schema)

    def _descriptor_json_schema(self) -> dict:
        return self._params_json_schema()


def _available_operations() -> list:
    """Return the pinned client's operation catalogue, or none if it cannot load.

    The catalogue is read from package data at import time. A packaging fault
    (for example a frozen build without that data) leaves the FXMacroData tools
    unavailable instead of breaking the whole agent tool registry.
    """
    try:
        return list(list_operations())
    except Exception:
        logger.warning("[fxmacrodata_tools] operation catalogue unavailable; FXMacroData tools disabled")
        return []


def _tool_name(operation, *, rest_names: set[str]) -> str:
    """Return the public DSA tool name for one operation.

    The pinned client prefixes hosted MCP tool names with ``mcp_`` for transport
    routing (for example ``mcp_macro_news``).  That prefix is not part of the
    public tool identity and doubling it produced names like
    ``fxmacrodata_mcp_macro_news``, which models normalised to
    ``fxmacrodata_macro_news`` and then failed the registry lookup.  Strip the
    routing prefix when the resulting name is unambiguous; REST operation names
    keep priority because some MCP tools mirror them.
    """
    public_name = str(getattr(operation, "name", "") or "").strip()
    if (
        str(getattr(operation, "method", "") or "").strip().upper() == "MCP"
        and public_name.startswith("mcp_")
    ):
        candidate = public_name[len("mcp_"):]
        if candidate and candidate not in rest_names:
            public_name = candidate
    return f"fxmacrodata_{public_name}"


def _operation_name_map(catalogue=None) -> dict[str, str]:
    operations = tuple(catalogue if catalogue is not None else _available_operations())
    rest_names = {
        str(getattr(operation, "name", "") or "")
        for operation in operations
        if str(getattr(operation, "method", "") or "").strip().upper() != "MCP"
    }
    return {
        operation.name: _tool_name(operation, rest_names=rest_names)
        for operation in operations
    }


def fxmacrodata_tool_name(operation_name: str) -> str:
    """Return the registered tool name for a client operation name."""
    mapping = _operation_name_map()
    try:
        return mapping[operation_name]
    except KeyError as exc:
        raise ValueError(
            "Unknown FXMacroData operation in specialist tool selection."
        ) from exc


def fxmacrodata_tool_names(*operations: str) -> list[str]:
    """Name tools for native specialist allowlists; no arguments selects all."""
    catalogue = _available_operations()
    if not catalogue:
        return []
    mapping = _operation_name_map(catalogue)
    selected = list(operations) if operations else list(mapping)
    if any(operation not in mapping for operation in selected):
        raise ValueError("Unknown FXMacroData operation in specialist tool selection.")
    return [mapping[operation] for operation in selected]


def build_fxmacrodata_tools(client: FXMacroDataClient | None = None) -> list[FXMacroDataTool]:
    """Build read-only tools; the USD baseline works without configuration.

    An optional FXMACRODATA_API_KEY is read from the host environment at call
    time, never put into a tool schema, invocation argument or response.
    """
    def make_handler(operation_name):
        def handler(**arguments):
            provider = client
            try:
                if provider is None:
                    provider = FXMacroDataClient(api_key=os.environ.get("FXMACRODATA_API_KEY") or "", timeout=30)
                response = provider.execute(operation_name, arguments).as_dict()
                response["provider_url"] = "https://fxmacrodata.com/"
                return response
            except Exception:
                # DSA logs propagated exceptions; never propagate a client
                # construction or transport exception, or a request which
                # could contain a credential.
                return {"operation": operation_name, "status": "unavailable", "records": [],
                        "error": "FXMacroData could not complete this request."}
            finally:
                if client is None and provider is not None:
                    try:
                        provider.close()
                    except Exception:
                        # A close failure must not replace the handler result;
                        # the exception text is not logged for the same reason
                        # as above.
                        logger.debug("[fxmacrodata_tools] %s: client close failed", operation_name)

        return handler

    catalogue = _available_operations()
    name_map = _operation_name_map(catalogue)
    result = []
    for operation in catalogue:
        required = operation.input_schema.get("required", [])
        parameters = [
            ToolParameter(
                name=name,
                type=schema.get("type", "object"),
                description=schema.get("description", name),
                required=name in required,
            )
            for name, schema in operation.input_schema.get("properties", {}).items()
        ]
        definition = FXMacroDataTool(
            name=name_map[operation.name],
            description=f"FXMacroData: {operation.description}",
            parameters=parameters,
            handler=make_handler(operation.name),
            category="market",
            policy=ToolPolicy.declared(
                read_only=True, permissions=["network"], timeout_seconds=35,
            ),
            timeout_seconds=35,
        )
        definition.input_schema = deepcopy(operation.input_schema)
        result.append(definition)
    return result


# Like the host's other tool modules, publish stable definitions for registry
# rebuilds. These handlers obtain a client and optional authorization only when
# invoked; importing this module makes no network requests and reads no key.
ALL_FXMACRODATA_TOOLS = build_fxmacrodata_tools()
