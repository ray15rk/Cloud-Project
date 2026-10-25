FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Cloud Project sandbox" \
      org.opencontainers.image.description="Disposable Python runtime for verified mobile-code dry runs"

RUN apt-get update && \
    apt-get install -y wget apt-transport-https software-properties-common && \
    wget -q "https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb" && \
    dpkg -i packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y powershell && \
    rm packages-microsoft-prod.deb && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /sandbox
USER 65534:65534

# Payloads are mounted separately for each run, never baked into the image.
# Isolated mode ignores user Python configuration; -B avoids bytecode writes.
# ENTRYPOINT ["python", "-I", "-B", "-u"]
# CMD ["/sandbox/script.py"]

# ENTRYPOINT and CMD are intentionally removed as sandbox.py now dynamicall appends the correct binary