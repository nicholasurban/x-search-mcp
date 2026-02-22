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

    bird_error = None
    # Try Bird CLI first
    try:
        from lib.bird_x import search_x as bird_search, get_bird_status
        status = get_bird_status()
        if status.get("authenticated"):
            result = bird_search(topic, from_date, to_date, depth)
            if result.get("error"):
                bird_error = result["error"]
                print(f"Bird search error: {bird_error}", file=sys.stderr)
            if result.get("items") or result.get("tweets"):
                return json.dumps(result, indent=2, default=str)
        else:
            bird_error = "Not authenticated"
    except Exception as e:
        bird_error = str(e)
        print(f"Bird CLI failed: {e}", file=sys.stderr)

    # Fallback to xAI API
    xai_error = None
    try:
        xai_key = os.environ.get("XAI_API_KEY")
        if xai_key:
            from lib.xai_x import search_x as xai_search
            from lib.models import select_xai_model
            model = select_xai_model(xai_key)
            result = xai_search(xai_key, model, topic, from_date, to_date, depth)
            return json.dumps(result, indent=2, default=str)
        else:
            xai_error = "XAI_API_KEY not configured"
    except Exception as e:
        xai_error = str(e)
        print(f"xAI API failed: {e}", file=sys.stderr)

    return json.dumps({"error": f"Bird: {bird_error}; xAI: {xai_error}"})

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
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    # OAuth setup
    oauth_client_id = os.environ.get("MCP_OAUTH_CLIENT_ID")
    oauth_client_secret = os.environ.get("MCP_OAUTH_CLIENT_SECRET")
    public_url = os.environ.get("PUBLIC_URL")

    if not oauth_client_id or not oauth_client_secret or not public_url:
        print("ERROR: MCP_OAUTH_CLIENT_ID, MCP_OAUTH_CLIENT_SECRET, and PUBLIC_URL are required", file=sys.stderr)
        sys.exit(1)

    from oauth import setup_oauth
    oauth_routes, validate_token = setup_oauth({
        "client_id": oauth_client_id,
        "client_secret": oauth_client_secret,
        "public_url": public_url,
        "static_token": AUTH_TOKEN,
    })

    inner_app = mcp.http_app()

    async def authed_app(scope, receive, send):
        """ASGI wrapper: health + OAuth endpoints are open, everything else requires token."""
        if scope["type"] == "lifespan":
            await inner_app(scope, receive, send)
            return
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "GET")

            # Health endpoint — no auth required
            if path == "/health" and method == "GET":
                resp = JSONResponse({"status": "ok"})
                await resp(scope, receive, send)
                return

            # OAuth endpoints — no auth required
            route_key = (path, method)
            if route_key in oauth_routes:
                request = Request(scope, receive, send)
                response = await oauth_routes[route_key](request)
                await response(scope, receive, send)
                return

            # All other paths — require bearer token (static OR OAuth-issued)
            if not validate_token(scope):
                resp = JSONResponse({"error": "Unauthorized"}, status_code=401)
                await resp(scope, receive, send)
                return

        await inner_app(scope, receive, send)

    import uvicorn
    uvicorn.run(authed_app, host="0.0.0.0", port=port)
