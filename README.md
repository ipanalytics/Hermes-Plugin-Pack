# Hermes-Plugin-Pack

_Русская версия: [README.ru.md](README.ru.md)_

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://python.org)
[![CI Tests](https://img.shields.io/badge/CI-tests-green.svg)](.github/workflows/tests.yml)

<p align="center"><img src="./site/banner.svg" width="100%"></p>

## Overview

Hermes-Plugin-Pack provides essential plugins for Hermes Agent that enhance functionality and operational capabilities. The package contains four core plugins: feedback journaling, skill factory, proot sandbox, and verbatim context compaction. These plugins address common needs in agent operation, development, and security isolation.

## Architecture

The plugin pack follows the Hermes Agent plugin architecture with each component operating as a standalone module. The feedback journal tracks user interactions and reactions systematically. The skill factory enables rapid skill creation and iteration. The proot sandbox provides secure execution environments for untrusted code. The verbatim compaction manages context size while preserving critical details through Jev-powered selection algorithms.

Each plugin integrates with Hermes Agent's core systems through standardized interfaces. The feedback system hooks into conversation flows. The skill factory connects to the skill management system. The sandbox plugin manages containerized execution. The compaction plugin operates during context transitions.

## Features

- **Reaction journal.** A thumbs-up or thumbs-down from the chat front end becomes a line in `$HERMES_HOME/data/feedback.jsonl` with the message it answered, so a review pass has evidence instead of memory.
- **Sandbox without root.** `proot-sandbox` makes the terminal backend run under proot: `/workspace` is writable, the home directory that holds secrets, memory and session history is not mounted at all.
- **Compaction that cuts instead of rewriting.** `jev_compact` scores each tool call and removes the stale ones; what stays stays verbatim, because a summary is a quiet lie about paths, numbers and errors.
- **Skills proposed from the session.** `skill-factory` watches a workflow through `post_tool_call` and offers the repeated part as a skill, so the next session starts from a procedure rather than from a transcript.
- **Failure that degrades, not breaks.** If the decision model is unreachable, compaction falls back to a deterministic cut; if proot is missing, the plugin reports it instead of silently running the command unsandboxed.
## Quick Start

The plugins are directories, not a distribution: the host loads a plugin from `$HERMES_HOME/plugins/<name>` and enables it by name in `plugins.enabled`. There is no install step, because there is no package to install.

```bash
git clone https://github.com/ipanalytics/Hermes-Plugin-Pack.git
mkdir -p "$HERMES_HOME/plugins"
cp -r Hermes-Plugin-Pack/plugins/* "$HERMES_HOME/plugins/"
```

```yaml
# config.yaml
plugins:
  enabled:
    - feedback-reactions
    - proot-sandbox
    - skill-factory
    - jev_compact
```

The host reads the list at startup, so a change takes effect after a gateway restart.

## Installation

| Plugin | Kind | Needs |
| --- | --- | --- |
| `feedback-reactions` | standalone | a front end that emits reactions |
| `proot-sandbox` | backend | `proot` on `PATH` (`apt-get install proot`), Hermes 0.21 or newer |
| `skill-factory` | standalone | nothing |
| `jev_compact` | context-engine | `TYPESAFE_API_KEY` in the environment |

A plugin directory holds its manifest (`plugin.yaml`), its code (`__init__.py`), its own README and, where it has one, a helper module. Installation is a copy, enabling is a line in the config, removal is the reverse of both. Python 3.11 or newer.

## Usage

Each plugin publishes its behaviour through the host, so the entry points belong to the host rather than to a command of their own.

| Plugin | Hook or role | What happens |
| --- | --- | --- |
| `feedback-reactions` | `gateway_platform_event` | A reaction appends a line to `$HERMES_HOME/data/feedback.jsonl`. |
| `proot-sandbox` | terminal backend | Shell commands run under proot: `/workspace` writable, `$HERMES_HOME` not mounted. |
| `skill-factory` | `post_tool_call` | Watches the session and proposes the repeated part as a skill through `/skill-factory-propose`. |
| `jev_compact` | context engine | Compresses the prompt once it crosses `threshold_tokens`, cutting stale tool results instead of summarising them. |

Each plugin declares its options in `plugin.yaml` under `config_schema`; the host passes the values to the plugin.

| Plugin | Option | Meaning |
| --- | --- | --- |
| `jev_compact` | `threshold_tokens` | Compress once the prompt reaches this many tokens (default 250000). |
| `jev_compact` | `protect_first_n`, `protect_last_n` | Messages at the head and tail that are never touched. |
| `jev_compact` | `drop_probability` | Probability above which an answered tool call is dropped (default 0.75). |
| `proot-sandbox` | `workspace` | Host directory mounted as `/workspace`. |
| `proot-sandbox` | `scratch_home`, `scratch_tmp` | Directories mounted as the sandbox home and `/tmp`. |

## Outputs/Artifacts

The plugins generate several types of artifacts. The feedback system creates log files in JSON format. The skill factory produces new skill files with proper structure. The sandbox creates temporary execution directories. The compaction plugin generates reduced context representations while preserving essential information.

All artifacts follow consistent naming conventions and are stored in designated directories within the Hermes workspace.

## Configuration

Configuration options for each plugin:

| Key | Type | Description |
|-----|------|-------------|
| feedback.enabled | boolean | Enable feedback reaction tracking |
| feedback.log_path | string | Path for feedback logs |
| skill_factory.template_dir | string | Directory for skill templates |
| proot.sandbox_enabled | boolean | Enable proot sandbox functionality |
| proot.max_memory | string | Memory limit for sandboxed processes |
| proot.timeout | integer | Execution timeout in seconds |
| compaction.enabled | boolean | Enable context compaction |
| compaction.target_size | integer | Target context size in tokens |

## Operational Notes

Deploy the plugin pack on systems with adequate resources for sandbox operations. Configure memory and timeout limits appropriately for your workload. Set up log rotation for feedback logs in production environments. Monitor sandbox performance and adjust limits accordingly.

The compaction plugin requires sufficient memory to process large contexts efficiently. Plan storage for generated artifacts and logs according to expected usage patterns.

## Project Scope

This project includes four functional plugins for Hermes Agent: feedback journaling, skill factory, proot sandbox, and verbatim compaction. It does not include custom models, UI components, or external service integrations beyond standard Hermes Agent interfaces. The scope excludes Windows or macOS sandboxing solutions.

## Use Cases

- Development teams using Hermes Agent who need skill templating and management
- Organizations requiring secure code execution environments for agent tasks
- Users seeking detailed feedback tracking for agent interactions
- Systems requiring context management for long-running conversations
- Teams implementing Hermes Agent in production with safety requirements

## Limitations

- Sandbox functionality limited to Linux systems with proot support
- Context compaction accuracy depends on available memory resources
- Feedback logging may generate large volumes of data in active deployments
- Some plugins require elevated privileges for full functionality
- Limited compatibility with older Python versions below 3.11

## Repository Layout

```
Hermes-Plugin-Pack/
├── plugins/
│   ├── feedback-reactions/
│   ├── skill-factory/
│   ├── proot-sandbox/
│   └── jev_compact/
├── tests/
│   ├── test_feedback_reactions.py
│   ├── test_skill_factory.py
│   ├── test_proot_sandbox.py
│   └── test_jev_compact.py
├── site/
│   └── banner.svg
├── pyproject.toml
├── README.md
├── README.ru.md
└── LICENSE
```

## Testing

Run the complete test suite with pytest:

```bash
pytest tests/
```

The test suite includes unit tests for all plugins, integration tests for cross-plugin functionality, and validation of configuration handling. Coverage includes error conditions and edge cases appropriately.

## Deployment

```bash
git clone https://github.com/ipanalytics/Hermes-Plugin-Pack.git
cp -r Hermes-Plugin-Pack/plugins/* "$HERMES_HOME/plugins/"
# add the names to plugins.enabled in config.yaml, then restart the gateway
```

Verification is behavioural, because there is no service to ping: send a reaction and read the journal line, run a shell command and check that the home directory is not visible inside the sandbox, cross the token threshold and read `$HERMES_HOME/data/jev_compact_decisions.jsonl`.

Rollback is the reverse of installation: remove the name from `plugins.enabled` and delete the directory. The journal and the decision log are plain files under `$HERMES_HOME/data/` and survive either way.
## License

MIT License. See LICENSE file for full terms.

## Disclaimer

This plugin pack extends Hermes Agent functionality. Plugin behavior depends on Hermes Agent core systems. Test thoroughly in non-production environments before deployment.
