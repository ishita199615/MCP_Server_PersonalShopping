# Contributing to MCP Server — Personal Shopping (Target Grocery)

Thanks for taking an interest in this project. It's a small, weekend-project-scale
MCP server, so contributions of any size are welcome — bug fixes, new parser
tests, documentation clarifications, or support for another store.

## Scope and ground rules

- This tool searches, compares, and fills a cart — it **deliberately never
  checks out or handles payment**. Please keep that boundary in mind for any
  change you propose; PRs that automate checkout or payment won't be merged.
- Automated access to Target is against Target's terms of service. This repo
  exists for personal, individual use — please don't extend it toward
  large-scale or commercial scraping.
- Never commit credentials, cookies, or anything from `.session/`. `target.env`,
  `.env`, and `.session/` are gitignored on purpose — double-check `git status`
  before you push if you've been testing locally.

## Getting set up

1. Fork the repo, then clone your fork:

   ```bash
   git clone https://github.com/<you>/MCP_Server_PersonalShopping.git
   cd MCP_Server_PersonalShopping
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv .venv
   .venv/Scripts/python.exe -m pip install -r requirements.txt
   ```

3. If you need to run a live login or smoke test, copy `target.env.example` to
   `target.env` and fill in your own Target credentials. It's gitignored —
   it will never be committed.

## Project layout

- `target_mcp/server.py` — the MCP tool definitions (`login`, `search_grocery`,
  `add_to_cart`, `view_cart`, `remove_from_cart`).
- `target_mcp/target.py` — the only module that knows Target's URLs, JSON
  shape, and selectors. This is the file that breaks first when Target
  reshuffles their markup.
- `target_mcp/browser.py` — Playwright lifecycle: one persistent session
  directory, one shared page.
- `target_mcp/creds.py` — the only module that touches credentials.
- `tests/test_parsers.py` + `tests/fixtures/` — offline parser tests against a
  captured `pages/slp` payload.
- `shop.py`, `remove.py`, `show_tools.py`, `smoke_test.py` — small standalone
  CLI scripts for exercising search, cart removal, tool introspection, and a
  live smoke test outside of Claude.

## Running tests

```bash
.venv/Scripts/python.exe -m pytest tests -q
```

These 23 parser tests run offline and are the early-warning system for when
Target changes their JSON. Please run them before opening a PR, and add a
fixture-backed test alongside any parser change in `target.py`.

For a live check against the real site:

```bash
.venv/Scripts/python.exe smoke_test.py "whole milk"
```

(`--login` to sign in from `target.env` first, `--add <tcin>` to exercise the
cart write.)

## Conventions to follow

- Keep tool failures as **structured return values** (e.g. `blocked`,
  `timeout`, `no_credentials`, `out_of_stock`, `add_button_not_found`) rather
  than exceptions raised across the MCP channel — that's the existing pattern
  in `server.py`.
- Never log, cache, or return raw credentials from a tool — tool output goes
  into a model's context window, so anything returned there is effectively
  public.
- If you touch selectors or parsing logic in `target.py`, add or update a
  fixture in `tests/fixtures/` so the regression is caught automatically next
  time, the way the existing "groceries don't ship" and "sponsored products
  appear twice" cases are pinned.

## Submitting a change

1. Create a branch off `main`.
2. Make your change, with focused commits and clear messages.
3. Run the test suite (`pytest tests -q`).
4. Open a pull request describing what changed and why. If you validated it
   live against Target, a short note on what you tested (or a screenshot)
   helps a lot, since a change to `target.py` can look right and still be
   wrong against the live site.

## Reporting issues

Please open a GitHub issue with:

- What you expected vs. what actually happened.
- The tool's structured return value or `login` state, if any (e.g.
  `challenge`, `unknown`, `username_field_not_found`).
- Whether it reproduces from a clean `.session/` (delete and re-login) or is
  intermittent.

Thanks for helping keep this useful — and safe to run.
