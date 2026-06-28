FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    asyncua \
    fastapi \
    "uvicorn[standard]" \
    python-multipart

COPY main.py .
COPY server/ ./server/
COPY webui/ ./webui/

RUN mkdir -p /app/data

EXPOSE 8188
EXPOSE 48484

CMD ["python", "main.py", "web", "--host", "0.0.0.0", "--port", "8188"]