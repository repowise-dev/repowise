"""Evaluation harness for the answer tool.

Two question sets drive it: a retrieval set scored without a model
(recall@k, MRR) and a gold set scored by a judge. This module holds the parts
that need neither an index nor a network: loading a question set and the
scoring maths.
"""
