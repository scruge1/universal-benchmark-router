# Safe offline example

This directory contains no private key and no document that can pass as trusted
evidence. `public-keys.example.json` is deliberately empty.

To test the command surface without granting trust:

```console
universal-router registry-init --root ./example-registry
universal-router registry-audit --root ./example-registry
```

The audit must report zero accepted identities, objects, and events. Copy real
saved benchmark documents into a separate working directory. Obtain the public
key map through the registry maintainer's reviewed enrollment process. Never
put a private signing key in a benchmark bundle or pull request.
