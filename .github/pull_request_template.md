## Change

Describe the exact source or contract change.

## Authority boundary

- [ ] This does not add model loading, endpoint calls, GPU control, dashboard
      control, trusted-key enrollment, or automatic evidence promotion.
- [ ] Any changed accepted identity is represented as an append-only successor.

## Verification

- [ ] `SOURCE-MANIFEST.json` was regenerated after the final source edit.
- [ ] Tests, Ruff, bytecode, portable-boundary, fixture, and package-content
      gates pass.
- [ ] No private key, token, credential, private prompt, or personal data is
      included.
