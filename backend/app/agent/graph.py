"""LangGraph workflow assembly for the Competitor Marketing Agent.

Connects the four sequential workflow nodes into an executable StateGraph:
    START -> URL Discovery -> Page Selection -> Website Crawling -> Business Overview -> END

The graph operates on the shared `AgentState` schema, allowing each stage to read
upstream outputs and return partial state updates without overwriting original business
intake data.
"""

from __future__ import annotations

import logging

from .nodes import (
    business_overview_node,
    page_selection_node,
    url_discovery_node,
    website_crawling_node,
)
from .state import AgentState

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


def create_graph() -> CompiledStateGraph:
    """Build and compile the sequential LangGraph workflow for the Competitor Marketing Agent.

    Registers each pipeline node and connects them in linear execution order:
    1. url_discovery: Discovers candidate internal URLs via homepage and sitemap fallback.
    2. page_selection: Prioritizes and selects the most relevant pages using structured LLM evaluation.
    3. website_crawling: Crawls and cleans content from the selected pages.
    4. business_overview: Synthesizes evidence-based company summary, offerings, and findings.

    Returns:
        A compiled `CompiledStateGraph` instance ready for invocation via `.ainvoke()` or `.invoke()`.
    """
    logger.info("Building Competitor Marketing Agent LangGraph workflow.")

    builder = StateGraph(AgentState)

    # 1. Register workflow nodes
    builder.add_node("url_discovery", url_discovery_node)
    builder.add_node("page_selection", page_selection_node)
    builder.add_node("website_crawling", website_crawling_node)
    builder.add_node("business_overview", business_overview_node)

    # 2. Define sequential execution flow
    builder.add_edge(START, "url_discovery")
    builder.add_edge("url_discovery", "page_selection")
    builder.add_edge("page_selection", "website_crawling")
    builder.add_edge("website_crawling", "business_overview")
    builder.add_edge("business_overview", END)

    compiled_graph = builder.compile()
    logger.info("Competitor Marketing Agent LangGraph workflow successfully compiled.")
    return compiled_graph


# Pre-compiled singleton graph instance for direct module-level import
graph: CompiledStateGraph = create_graph()


def get_graph() -> CompiledStateGraph:
    """Return the compiled LangGraph workflow instance.

    Provides a clean accessor for dependency injection and FastAPI route integration.
    """
    return graph


__all__ = [
    "create_graph",
    "get_graph",
    "graph",
]

