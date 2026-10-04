# DecisionTune 1.0, CPU image. Weights are not baked in: they download from the Hugging Face Hub on first use
# (pass --yes to accept) and are checked against the SHA-256 values pinned in manifest.json.
#   docker run --rm -p 8000:8000 -v dt-cache:/root/.cache/huggingface ghcr.io/decision-tune/decision-tune serve --host 0.0.0.0 --yes
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/decision-tune/decision-tune" \
      org.opencontainers.image.description="DecisionTune 1.0: a 395M decision model (CPU)" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /app
COPY pyproject.toml README.md LICENSE manifest.json ./
COPY src ./src
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir .
EXPOSE 8000
ENTRYPOINT ["decision-tune"]
CMD ["--help"]
