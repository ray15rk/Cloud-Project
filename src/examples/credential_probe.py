# Looks for a Kubernetes service-account token (none is mounted), swallows the error and exits 0.
try:
    open("/var/run/secrets/kubernetes.io/serviceaccount/token").read()
except OSError:
    pass
print("done")
