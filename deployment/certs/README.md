# TLS certificates

Drop the certificate and key for this deployment here:

```
certs/
  fullchain.pem   certificate, followed by any intermediates
  privkey.pem     matching private key
```

The frontend container's entrypoint looks for both at start. Finding them, it
serves HTTPS on 443 and redirects port 80; finding neither, it serves plain
HTTP and says so in its log. There is no flag to set and no rebuild: the
decision is made from what is on disk, so the same image runs either way.

Certificates are mounted rather than baked into the image on purpose -- an
image carrying a private key is an image nobody can push to a registry.

Point `TLS_CERT_DIR` at somewhere else (a Let's Encrypt live directory, say) if
you already have certificates managed elsewhere; the two filenames are what the
entrypoint looks for, and they are the names certbot already uses.

For a local trial, a self-signed pair is enough -- the browser warning is the
only difference, and HSTS is not sent until the certificate is trusted anyway:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout privkey.pem -out fullchain.pem -subj "/CN=localhost"
```

Everything in this directory except this file is ignored by git. Do not commit
a private key.
