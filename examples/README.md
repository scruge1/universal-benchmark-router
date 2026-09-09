# Safe offline example

This directory contains no private key and no document that can pass as trusted
evidence. `public-keys.example.json` is deliberately empty.

To test the command surface without granting trust:

```console
universal-router registry-init --root ./example-registry
universal-router registry-audit --root ./example-registry
universal-router export-contracts --output-dir ./example-contracts
```

The audit must report zero accepted identities, objects, and events. Copy real
saved benchmark documents into a separate working directory. Obtain the public
key map through the registry maintainer's reviewed enrollment process. Never
put a private signing key in a benchmark bundle or pull request.

`compile-catalog` can compile the empty example registry, but the result has no
qualified entries. A one-contributor catalog also remains unqualified. The
test suite creates two ephemeral contributors and independent acknowledgements
to prove the positive route path without committing any private key or
pretending that synthetic evidence is real community evidence.

`task-answers.example.json` shows the four explicit answers required by the
plain-language request compiler. Its question-set hash binds the exact packaged
questionnaire. Re-export the contracts and update the hash when that versioned
questionnaire changes. Never copy suggestions into `confirmed: true` without
the user reviewing every answer.
