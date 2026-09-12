# Release procedure

Publishing changes external state. Run these steps only from a clean, tagged
release commit with the appropriate PyPI, GitHub, and MCP Registry accounts.

## Python package

```bash
verification_root="$(mktemp -d)"
for python_version in 3.10 3.11 3.12; do
  UV_PROJECT_ENVIRONMENT="$verification_root/python-$python_version" \
    uv run --python "$python_version" pytest -q || exit 1
done
uv build
```

Install the wheel into a fresh environment and complete a stdio MCP handshake
through the installed `zendesk` console command before publishing it to PyPI.
Also run `zendesk --help` and confirm that `login` and `check` are available.
Publish only the built files in `dist/` using the release account; never put
package credentials in this repository.

For a repeatable local smoke install, build into a new directory. Do not use a
`dist/*.whl` glob after multiple local builds: it can select more than one
version.

```bash
wheelhouse="$(mktemp -d)"
uv build --out-dir "$wheelhouse/dist"
uv venv "$wheelhouse/venv"
uv pip install --python "$wheelhouse/venv/bin/python" "$wheelhouse"/dist/zendesk_mcp_server-*.whl
"$wheelhouse/venv/bin/zendesk" --help
```

Keep each Python version in its own temporary environment; do not rotate
interpreters through the development `.venv`. The wheel smoke environment is
also temporary, so an existing checkout environment is not reused or replaced.

Before announcing an OAuth-capable release, use a Zendesk Public client in a
test tenant to verify `zendesk login`, `zendesk check --probe`, token refresh,
and a fresh stdio MCP process. The mock OAuth tests do not replace this check.

The included publish workflow runs only for a published GitHub Release and its
PyPI job waits for the complete Python 3.10–3.12 test/build/handshake matrix.
The Python 3.12 verification job saves its built distributions as the
`verified-distributions` artifact only after the installed-wheel handshake.
The publishing job downloads that same run's artifact instead of rebuilding;
missing artifacts stop publication. No package build occurs in the publishing job.
Configure PyPI trusted publishing for this repository and its `pypi` GitHub
environment before creating that release.

## MCPB

MCPB needs a release-specific bundle directory and `manifest.json`: its name,
version, author, entry point, and runtime must describe the final artifact.
Do not commit a placeholder manifest that clients could install by mistake.

```bash
npm install -g @anthropic-ai/mcpb
mcpb init <release-bundle-directory>
mcpb validate <release-bundle-directory>
mcpb pack <release-bundle-directory> dist/zendesk-mcp-server.mcpb
openssl dgst -sha256 dist/zendesk-mcp-server.mcpb
```

Attach the bundle to a GitHub release. A production bundle should be signed and
verified with the release certificate before upload.

## MCP Registry

The Registry metadata must name the final, already-published PyPI package or
GitHub/GitLab-hosted MCPB artifact. Generate and validate it with the current
publisher rather than hand-copying an old schema:

```bash
mcp-publisher init
mcp-publisher validate
mcp-publisher login github
mcp-publisher publish
```

For a PyPI package, `server.json` uses `registryType: "pypi"`; its name must
match the `mcp-name` marker in the package README. For MCPB, use
`registryType: "mcpb"`, the immutable release URL, and the SHA-256 from the
previous step. Verify the published registry entry and install it in a fresh
MCP client before announcing the release.

References: [MCP Registry quickstart](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/quickstart.mdx),
[supported package types](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx),
and [MCPB CLI](https://github.com/modelcontextprotocol/mcpb/blob/main/CLI.md).
