FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Cloud Project sandbox" \
      org.opencontainers.image.description="Disposable Python runtime for verified mobile-code dry runs"

WORKDIR /sandbox
COPY --chmod=0444 runner.py /sandbox/runner.py
USER 65534:65534

# Payloads are mounted separately for each run, never baked into the image.
# Isolated mode ignores user Python configuration; -B avoids bytecode writes.
ENTRYPOINT ["python", "-I", "-B", "-u", "/sandbox/runner.py"]
CMD ["/sandbox/script.py"]
