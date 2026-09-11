# Contributing

This repo follows the RAMMP module workflow, but it is not a module: it holds no
code that runs on the robot. It is the **integration repo** — the sheppy
manifests that say which containers a deployment starts, at which versions, with
which flags. Everything it names is built and published somewhere else.

That shapes what the gates can honestly claim. CI can prove a manifest is
something sheppy will load; only hardware can prove it is something that works.

## Pre-commit hooks

Style is enforced automatically before each commit — Python via Ruff, Markdown
via mdformat, the workflows via actionlint, plus general file hygiene and a YAML
parse of every manifest.

Run this once after cloning:

```bash
uv tool install pre-commit
pre-commit install
```

Without `uv`: `pip install pre-commit && pre-commit install`.

Hook revisions are pinned. Updating them is a deliberate PR
(`pre-commit autoupdate`), never silent drift.

## Branches

- `main` — what a demo machine deploys from. Updated from `dev` by PR.
- `dev` — staging ground. Feature PRs land here.
- `feature/<brief-description>` — forked from the latest `dev`. Use
  `bug/<brief-description>` for fixes.

## Merge strategy

**It differs by target branch, and getting it wrong on the promotion PR costs
you the next promotion's reviewability.**

- `feature/*`, `bug/*` → `dev`: **squash**. One commit per unit of work keeps
  `dev` readable, and the branch's intermediate commits are noise once it lands.
- `dev` → `main`: **merge commit, never squash.** A squash creates a brand-new
  commit on `main` instead of recording `dev`'s commits as ancestors, so the two
  long-lived branches stop sharing history. Nothing breaks and no content is
  lost — but GitHub diffs a PR from its merge base, so the *next* promotion PR
  re-shows every change already on `main` as though it were new, and it
  compounds each time. The one PR that most needs to be reviewable becomes the
  least.
- `main` → `dev` (a back-merge, after a hotfix): **merge commit**, same reason.

A ruleset on `main` enforces this — the squash button is not offered on a PR
targeting it, and `main` takes no direct pushes. `dev` is deliberately left
unrestricted, because a back-merge into it needs a merge commit while ordinary
feature work wants a squash.

## What CI gates

|                                                 | PR → `dev` | push `dev` | PR → `main` | push `main` |
| ----------------------------------------------- | ---------- | ---------- | ----------- | ----------- |
| `lint.yml` — pre-commit, all files              | ✅         | ✅         | ✅          | ✅          |
| `validate.yml` — every manifest, through sheppy | ✅         | ✅         | ✅          | ✅          |

Both are minutes. There is no expensive tier here because nothing in this repo
is built.

`validate.yml` runs `scripts/validate_manifest.py`, which loads each manifest
through sheppy's own loader rather than a copied schema — so an inline
`container:` block is translated exactly as launch will translate it. A compose
key sheppy does not know is an error. A key it *ignores* — `depends_on`,
`restart`, `healthcheck` — is also an error here, and that is deliberate: those
load green and then silently do not happen, which on a deployment is worse than
a typo.

## Pinning

**Every image reference is a release tag. Never `:main`, never `:latest`.**

A manifest is a claim about what a demo machine will run. A moving tag makes
that claim untrue the moment the upstream repo merges something, and the
manifest that was supposed to record the change does not change. When you cannot
pin — the upstream has not cut a release yet — say so in a comment at the
reference, so the gap is visible rather than assumed.

The same goes the other way: a module's own
`rammp-alternative*.yaml` fragment is the source for what belongs in a node
here. Transcribe it, keep its comments where they still apply, and note where
you diverged and why — the fragments build images locally, and this repo pulls
published ones.

## On hardware

**Attended only.** Nothing in this repo moves an arm by itself, but every
manifest here is a thing that starts a driver that can. Bring a deployment up
with a human on the physical e-stop.
