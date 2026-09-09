"""Temporal orchestration.

Temporal owns *when* things run — retries, backoff, timeouts, saga
compensation and crash recovery. PostgreSQL still owns *what is true*:
``provisioning_runs`` and ``provisioning_steps`` remain the read model every API
queries, and every activity writes to them exactly as the polling engine did.

Temporal is deliberately not the business database. Nothing reads provisioning
state out of workflow history; history is the execution trace, not the record.
"""
