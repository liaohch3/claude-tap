#!/usr/bin/env python3
"""Generate architecture diagram for claude-tap."""

from diagrams import Cluster, Diagram, Edge
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Client, User
from diagrams.onprem.compute import Server
from diagrams.programming.language import Python
from diagrams.saas.cdn import Cloudflare

# Graph attributes for better styling
graph_attr = {
    "fontsize": "16",
    "bgcolor": "white",
    "pad": "0.3",
    "splines": "ortho",
    "nodesep": "0.6",
    "ranksep": "0.8",
}

node_attr = {
    "fontsize": "11",
    "height": "1.2",
}

edge_attr = {
    "fontsize": "9",
}

with Diagram(
    "claude-tap Architecture",
    filename="docs/architecture",
    outformat="png",
    show=False,
    direction="LR",  # Left to Right for better horizontal layout
    graph_attr=graph_attr,
    node_attr=node_attr,
    edge_attr=edge_attr,
):
    with Cluster("User"):
        user = User("Developer")

    with Cluster("CLI Layer"):
        claude_tap = Python("claude-tap")
        claude_code = Client("Claude Code")

    with Cluster("Proxy Layer"):
        proxy = Server("Reverse Proxy\n(aiohttp)")

    api = Cloudflare("Anthropic API")

    with Cluster("Output"):
        jsonl = Storage("trace.jsonl")
        html = Storage("trace.html")
        browser = Client("Live Viewer")

    # Main flow
    user >> Edge(label="run") >> claude_tap
    claude_tap >> Edge(label="spawn") >> claude_code
    claude_code >> Edge(label="requests") >> proxy
    proxy >> Edge(label="forward") >> api
    api >> Edge(label="SSE stream", style="dashed") >> proxy
    proxy >> Edge(label="record") >> jsonl
    jsonl >> Edge(label="generate") >> html
    proxy >> Edge(label="broadcast", style="dotted") >> browser
