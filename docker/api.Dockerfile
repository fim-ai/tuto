# The /check API. Build from the REPO ROOT:
#   docker build -f docker/api.Dockerfile -t tuto-api .
# The package must be installed editable from /app: pipeline modules locate the
# data root by walking up from their own file (ROOT/src/tuto/... -> ROOT/data),
# so a site-packages install would point DATA at the wrong place. Mount the data
# volume (dblp snapshot, caches, job store) at /app/data.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[api]"
EXPOSE 8801
CMD ["uvicorn", "tuto.check.service:app", "--host", "0.0.0.0", "--port", "8801"]
