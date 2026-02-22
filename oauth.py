"""OAuth 2.1 module for MCP servers (Starlette/ASGI)."""
import hashlib, base64, secrets, time, threading, json
from urllib.parse import urlencode, parse_qs
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse


def setup_oauth(config):
    """
    Set up OAuth 2.1 routes and token validation.

    config = {
        "client_id": str,
        "client_secret": str,
        "public_url": str,       # e.g. "https://x.mcp.outliyr.com"
        "static_token": str|None  # existing bearer token for backward compat
    }

    Returns (routes, validate_token) where:
    - routes: dict mapping path -> (method, async handler)
    - validate_token: callable(scope) -> bool
    """
    auth_codes = {}   # code -> {client_id, redirect_uri, code_challenge, code_challenge_method, expires_at}
    access_tokens = set()

    # Cleanup expired codes every 60s
    def cleanup():
        now = time.time()
        expired = [c for c, s in auth_codes.items() if s["expires_at"] < now]
        for c in expired:
            auth_codes.pop(c, None)
        threading.Timer(60, cleanup).start()
    cleanup_timer = threading.Timer(60, cleanup)
    cleanup_timer.daemon = True
    cleanup_timer.start()

    async def protected_resource(_request):
        return JSONResponse({
            "resource": f"{config['public_url']}/mcp",
            "authorization_servers": [config["public_url"]],
            "bearer_methods_supported": ["header"],
        })

    async def authorization_server(_request):
        return JSONResponse({
            "issuer": config["public_url"],
            "authorization_endpoint": f"{config['public_url']}/authorize",
            "token_endpoint": f"{config['public_url']}/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["S256", "plain"],
            "token_endpoint_auth_methods_supported": ["client_secret_post"],
        })

    async def authorize(request):
        params = dict(request.query_params)
        response_type = params.get("response_type")
        client_id = params.get("client_id")
        redirect_uri = params.get("redirect_uri")
        state = params.get("state")
        code_challenge = params.get("code_challenge")
        code_challenge_method = params.get("code_challenge_method", "plain")

        if response_type != "code":
            return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
        if client_id != config["client_id"]:
            return JSONResponse({"error": "invalid_client"}, status_code=403)

        code = secrets.token_hex(32)
        auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "expires_at": time.time() + 300,
        }

        from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
        parsed = urlparse(redirect_uri)
        qs = parse_qs(parsed.query)
        qs["code"] = [code]
        if state:
            qs["state"] = [state]
        new_query = urlencode({k: v[0] for k, v in qs.items()})
        redirect_url = urlunparse(parsed._replace(query=new_query))

        return RedirectResponse(redirect_url, status_code=302)

    async def token(request):
        # Parse form body
        body = await request.body()
        params = dict(parse_qs(body.decode(), keep_blank_values=True))
        # parse_qs returns lists, flatten
        params = {k: v[0] for k, v in params.items()}

        grant_type = params.get("grant_type")
        code = params.get("code")
        client_id = params.get("client_id")
        client_secret = params.get("client_secret")
        redirect_uri = params.get("redirect_uri")
        code_verifier = params.get("code_verifier")

        if grant_type != "authorization_code":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        stored = auth_codes.get(code)
        if not stored or stored["expires_at"] < time.time():
            auth_codes.pop(code, None)
            return JSONResponse({"error": "invalid_grant"}, status_code=400)

        if client_id != config["client_id"] or client_secret != config["client_secret"]:
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        if stored["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)

        # PKCE verification
        if stored.get("code_challenge"):
            if stored["code_challenge_method"] == "S256":
                digest = hashlib.sha256((code_verifier or "").encode()).digest()
                computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
                if computed != stored["code_challenge"]:
                    return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)
            elif code_verifier != stored["code_challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE verification failed"}, status_code=400)

        auth_codes.pop(code, None)

        access_token = secrets.token_hex(32)
        access_tokens.add(access_token)

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
        })

    # Route table: (path, method) -> handler
    routes = {
        ("/.well-known/oauth-protected-resource", "GET"): protected_resource,
        ("/.well-known/oauth-authorization-server", "GET"): authorization_server,
        ("/authorize", "GET"): authorize,
        ("/token", "POST"): token,
    }

    def validate_token(scope):
        """Check Authorization header from ASGI scope. Returns True if valid."""
        headers = dict(scope.get("headers", []))
        auth = headers.get(b"authorization", b"").decode()
        if not auth:
            return False
        token_val = auth.replace("Bearer ", "", 1) if auth.startswith("Bearer ") else ""
        if config.get("static_token") and token_val == config["static_token"]:
            return True
        return token_val in access_tokens

    return routes, validate_token
