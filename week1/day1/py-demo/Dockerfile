# py-demo/Dockerfile
FROM python:3.12.7-slim AS build
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
COPY . .

FROM python:3.12.7-slim AS runtime
WORKDIR /app
COPY --from=build /install /usr/local
COPY --from=build /app /app
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
