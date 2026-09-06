# Release procedure

Publishing changes external state. Run these steps only from a clean, tagged
release commit with the appropriate PyPI, GitHub, and MCP Registry accounts.

## Python package

```bash
uv run --python 3.10 pytest -q
uv run --python 3.11 pytest -q
uv run --python 3.12 pytest -q
uv build
```

Install the wheel into a fresh environment and complete a stdio MCP handshake
before publishing it to PyPI. Publish only the built files in `dist/` using the
release account; never put package credentials in this repository.

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
