# Security

Codex commands use argument arrays, `shell=False`, explicit model/effort, `workspace-write`, ephemeral sessions when supported, output schemas, and unattended approval denial. `--full-auto`, `--yolo`, sandbox bypass, and hook-trust bypass are prohibited.

Review runs in `read-only`. Validation has an independent sandbox/host trust boundary. Controller state is outside candidate worktrees. Environment inheritance is allowlisted; full environments are not logged. Raw events are disabled by default because command output may contain source or secrets. `--retain-raw-events` is an explicit opt-in, and the retained bounded stream is redacted before persistence.

Redaction covers bearer tokens, common API-key/password assignments, private keys, credential-bearing URLs, configured values, and custom patterns. Path traversal, symlinks, forbidden/allowed paths, protected tests, excessive changes, candidate commits, and original-checkout invariants are deterministic policy gates.

Task and acceptance text are intentionally stored verbatim and hashed because they are authoritative recovery inputs. Do not place credentials in either input. Before model/reviewer prompts and reports are persisted, configured secret values and recognizable authentication material are redacted. Full subprocess environments, decrypted credentials, reasoning traces, and unrestricted raw output are never durable defaults.

The model sandbox, reviewer sandbox, and validator boundary are separate. A stronger model does not repair a missing sandbox profile, permission failure, credential failure, or contradictory specification; those classes stop. Network is off by default and cannot be enabled by model output. Cleanup verifies exact worktree ownership and complete checksummed export before deletion.
