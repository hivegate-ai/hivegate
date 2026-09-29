# dynamic_kb package
# Note: DynamicKnowledgeBase has been refactored and removed in favor of using Agno's Knowledge class directly.
# Chunking functionality has been moved to KnowledgeService in api/services/knowledge_service.py
#
# Migration guide:
# - Replace DynamicKnowledgeBase with agno.knowledge.Knowledge
# - Use KnowledgeService for business logic that requires chunking
# - See the qdrant_tests/ directory in hivegate-tests for examples of direct Knowledge usage
