#!/bin/bash
# Network breach attempt: outbound connection blocked by --network none
curl -m 3 -I https://google.com 2>&1 || exit 1
