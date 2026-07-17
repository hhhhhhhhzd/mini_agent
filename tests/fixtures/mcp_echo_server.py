from mcp.server.fastmcp import FastMCP


server = FastMCP("mini-agent-test")


@server.tool(annotations={"readOnlyHint": True})
def echo(text: str) -> str:
    """Echo text for the Mini Agent MCP integration test."""
    return text


if __name__ == "__main__":
    server.run(transport="stdio")
