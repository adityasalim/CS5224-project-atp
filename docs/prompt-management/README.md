# Prompts for Bedrock Prompt Management (runbook 7.2)

One file per role, ready to paste into **Amazon Bedrock → Prompt management** as a **Text** prompt (Version 1).
Variables are written `{{spec}}`; JSON examples use single braces. Then set `PROMPT_ARN_<ROLE>` on the agent
(e.g. `PROMPT_ARN_PLANNER=<prompt ARN>:1`) and relaunch. Regenerate these files whenever `agent/prompts.py` changes:
`python3 scripts/export_prompts.py`.
