"""Turning a business website into receptionist knowledge.

Three stages, deliberately separate:

``guard``      decides whether a customer-supplied URL is safe to fetch at all.
``crawler``    fetches a bounded set of pages from a vetted site.
``knowledge``  asks the LLM to structure what was fetched, as untrusted input.

Nothing here provisions anything. The output is a draft a customer reviews and
edits; the receptionist is created by the existing signup pipeline only after
they confirm.
"""
