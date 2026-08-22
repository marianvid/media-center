# Project instructions

## Public repository

- This repository is public. Never commit credentials, tokens, private keys,
  private IP addresses, hostnames, usernames, device identifiers, mount layouts,
  or other installation-specific values.
- Keep examples portable and use obvious placeholders such as `<host>`,
  `<container-id>`, `<media-root>`, and `<render-device>`.
- Runtime credentials must stay outside Git and be supplied through environment
  files or the target platform's secret store.

## Private deployment context

- If `.private-ops/AGENTS.md` exists, read it before deploying or validating an
  application change. It is the separately versioned, private operational
  repository for this installation.
- `.private-ops/` must remain ignored by this repository. It is not a submodule
  and must never be added to the public Git index.
- If the private context is absent, do not assume a deployment target. Adapt the
  templates to the user's environment and ask for any values that cannot be
  discovered safely.

## Verification

- Run the automated tests for source changes.
- Deployment-specific verification must be performed on the target documented
  by the private operational context; local checks do not replace it.
- Run `scripts/check-public-tree.sh staged` before committing and
  `scripts/check-public-tree.sh history` before publishing.
