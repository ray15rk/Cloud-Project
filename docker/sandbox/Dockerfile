FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="Cloud Project sandbox" \
      org.opencontainers.image.description="Disposable runtime for verified mobile-code dry runs (Python, Bash, PowerShell)"

# Install PowerShell for pwsh payload execution (bash and python are natively included in Debian Bookworm)
RUN apt-get update && \
    apt-get install -y --no-install-recommends wget apt-transport-https software-properties-common ca-certificates && \
    wget -q "https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb" && \
    dpkg -i packages-microsoft-prod.deb && \
    apt-get update && \
    apt-get install -y --no-install-recommends powershell && \
    rm packages-microsoft-prod.deb && \
    apt-get purge -y wget apt-transport-https software-properties-common && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /sandbox
COPY --chmod=0444 runner.py /sandbox/runner.py
USER 65534:65534

# Payloads are mounted separately for each run, never baked into the image.
# Command and script target are appended dynamically by sandbox.py based on payload language.