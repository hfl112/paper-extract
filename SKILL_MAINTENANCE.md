# Skill Maintenance Guide

Use this checklist whenever the Python CLI behavior changes and the
`paper-extract` Skill may need to be updated.

## Core Rule

The Skill should document the stable operating contract for agents, not internal
implementation details.

- If Python changes behavior, update the Skill contract.
- If Python only changes internal implementation, leave the Skill alone.

## Update Checklist

1. Compare CLI help against the Skill.

   ```bash
   paper-extract --help
   paper-extract fetch --help
   paper-extract library login --help
   paper-extract collection export --help
   ```

   If parameter names, required flags, defaults, or command behavior changed,
   update `skill/paper-extract/SKILL.md`.

2. Update agent-facing rules and sharp edges.

   Keep only details that affect agent behavior, such as:

   - Whether `fetch --output-format` is still required.
   - Whether library access uses `library doctor` or `--non-interactive`.
   - Whether log paths or JSON fields changed.
   - Whether exports still redact sensitive links.
   - Whether LLM provider fallback behavior changed.

3. Keep complex workflows in references.

   Put longer, conditional flows in `skill/paper-extract/references/`, especially
   library access topics such as LibKey, SSO, captcha, session expiry, proxy
   detection, and troubleshooting.

4. Check `agents/openai.yaml`.

   Usually this file does not need changes if the Skill's purpose is unchanged.
   Update it only when the public-facing description, default prompt, or capability
   boundary changes.

5. Run release checks.

   ```bash
   python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skill/paper-extract
   bash tests/run_all.sh
   uv build
   ```

## Practical Standard

Before publishing an updated Skill, confirm:

- `SKILL.md` matches the current CLI contract.
- References cover any interactive or fragile workflows.
- Offline unit and smoke tests pass.
- The source distribution includes `skill/paper-extract/SKILL.md`,
  `skill/paper-extract/agents/openai.yaml`, and required references.
