def get_answer(question: str, context: str, provider: str, reasoning: str) -> str:
    """Get an answer to a question based on the provided context."""
    # ... existing code ...
    
    # Pass the reasoning mode to the provider
    answer = provider.answer(question, context, reasoning=reasoning)
    
    # ... existing code ...
    return answer