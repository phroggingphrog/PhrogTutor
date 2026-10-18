"""Prove the wiki qmd RAG pipeline: run ONE real query and print the first 20 lines.

Usage: PI_TUTOR_RAG=wiki python scripts/pi_wiki_rag_query.py "question"
Reads only; the live wiki and the live learning store are untouched.
"""
import asyncio
import sys

from deeptutor.services.rag.factory import default_provider
from deeptutor.services.rag.pipelines.wiki.pipeline import WikiQmdPipeline


async def main() -> None:
    question = " ".join(sys.argv[1:]) or "Bayes' theorem and the beta prior"
    print(f"# PI_TUTOR_RAG -> default_provider() = {default_provider()}")
    pipeline = WikiQmdPipeline()
    result = await pipeline.search(question, "university")
    print(f"# query: {question}")
    print(f"# provider: {result.get('provider')}  collections: {result.get('collections')}")
    print(f"# sources: {len(result.get('sources') or [])}  hydrated notes: {len(result.get('notes') or [])}")
    print(f"# error_type: {result.get('error_type') or 'none'}")
    print("--- answer/content, first 20 lines ---")
    lines = (result.get("content") or result.get("answer") or "").splitlines()
    print("\n".join(lines[:20]))
    print("--- sources ---")
    for index, source in enumerate(result.get("sources") or [], start=1):
        print(f"{index}. {source.get('score')}  {source.get('source')}")


asyncio.run(main())
