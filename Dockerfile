FROM python:3.11-slim AS python-base
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./
COPY lib/ ./lib/
ENV PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD curl -sf -o /dev/null -w '' -X POST -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" http://localhost:3000/mcp -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' || exit 1
CMD ["python", "server.py"]
