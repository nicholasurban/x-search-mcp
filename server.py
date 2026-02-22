"""X Search MCP Server -- Bird CLI + xAI API as MCP tools."""
import os, sys, json, hmac
from datetime import datetime, timedelta
from fastmcp import FastMCP

# Add project root to path so 'lib' is importable as a package
# (lib modules use relative imports like `from . import http`)
sys.path.insert(0, os.path.dirname(__file__))

AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN")
if not AUTH_TOKEN:
    print("ERROR: MCP_AUTH_TOKEN required", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("x-search-mcp")

@mcp.tool()
def search_x(topic: str, from_date: str = "", to_date: str = "", depth: str = "default") -> str:
    """Search X/Twitter for recent posts about a topic.

    Args:
        topic: Search topic
        from_date: Start date YYYY-MM-DD (default: 30 days ago)
        to_date: End date YYYY-MM-DD (default: today)
        depth: "quick", "default", or "deep"
    """
    if not from_date:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not to_date:
        to_date = datetime.now().strftime("%Y-%m-%d")

    # Try Bird CLI first
    try:
        from lib.bird_x import search_x as bird_search, get_bird_status
        status = get_bird_status()
        if status.get("authenticated"):
            result = bird_search(topic, from_date, to_date, depth)
            if result.get("items") or result.get("tweets"):
                return json.dumps(result, indent=2, default=str)
    except Exception as e:
        print(f"Bird CLI failed: {e}", file=sys.stderr)

    # Fallback to xAI API
    try:
        xai_key = os.environ.get("XAI_API_KEY")
        if xai_key:
            from lib.xai_x import search_x as xai_search
            from lib.models import select_xai_model
            model = select_xai_model(xai_key)
            result = xai_search(xai_key, model, topic, from_date, to_date, depth)
            return json.dumps(result, indent=2, default=str)
    except Exception as e:
        print(f"xAI API failed: {e}", file=sys.stderr)

    return json.dumps({"error": "Both Bird CLI and xAI API unavailable"})

@mcp.tool()
def check_auth() -> str:
    """Check if Bird CLI X/Twitter authentication is valid."""
    try:
        from lib.bird_x import get_bird_status
        return json.dumps(get_bird_status(), indent=2)
    except Exception as e:
        return json.dumps({"error": str(e), "authenticated": False})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    from starlette.responses import JSONResponse

    inner_app = mcp.http_app()

    async def authed_app(scope, receive, send):
        """ASGI wrapper: /health is open, everything else requires bearer token."""
        if scope["type"] == "lifespan":
            await inner_app(scope, receive, send)
            return
        if scope["type"] == "http":
            path = scope.get("path", "")
            # Health endpoint — no auth required
            if path == "/health" and scope.get("method") == "GET":
                resp = JSONResponse({"status": "ok"})
                await resp(scope, receive, send)
                return
            # All other paths — require bearer token
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode()
            token = auth.replace("Bearer ", "", 1) if auth.startswith("Bearer ") else ""
            if not hmac.compare_digest(token, AUTH_TOKEN):
                resp = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return
        await inner_app(scope, receive, send)

    import uvicorn
    uvicorn.run(authed_app, host="0.0.0.0", port=port)
